# Maven Stack Upgrade Tool — 아키텍처

- 작성일: 2026-08-07 (2026-08-11 개정: Stage 0 버전 확인 게이트, 작업 취소/삭제, 파일별 diff 뷰어, LLM 모델 설정 반영. 2026-08-12 개정: job 상태 stage1/stage2 분리 + 1단계 인수인계 후 재개, 사내 parent POM 목표 버전 전이, `index.html` 제출 폼/설명 콘텐츠와 `job.html` 진행 상황 뷰 분리 반영)
- 이 문서는 실제 구현(`backend/`, `frontend/`)을 기준으로 정리한 아키텍처 문서다. 설계 배경/의사결정 근거는 [`docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md`](superpowers/specs/2026-08-06-oss-dependency-governance-design.md)(이하 "설계 스펙")를 참고한다. 이 문서는 그 스펙이 실제로 어떤 모듈/파일로 구현됐는지를 코드 기준으로 매핑한다.

## 1. 한 줄 요약

임의의 Maven(Java) 프로젝트를 Git URL 또는 ZIP으로 입력받아, **Java 21 / Spring Boot 4.1 / Spring Cloud 2025.1 / Spring AI 2.0**으로 단계적으로 마이그레이션하고(1단계, 필수), 이후 남은 개별 OSS 취약점을 스캔·패치하는(2단계, 옵션) 로컬 실행 도구. 결과는 diff/리포트/AI 인수인계 가이드로 산출되며, **자동 커밋/푸시는 하지 않는다.**

## 2. 리포지토리 구조

```
backend/     FastAPI 백엔드 — 이 도구의 실제 구현체
  app/
    api/            REST 라우터 + 인증 의존성
    ingest/          Git/ZIP 들어옴(인입,引入), Maven 프로젝트 감지
    checkpoint/      work/ 디렉토리의 git 기반 체크포인트/롤백
    mvnrewrite/      mvn/OpenRewrite 서브프로세스 래퍼, 버전 파싱, 레시피 카탈로그
    orchestration/   LangGraph 상태 머신(1·2단계 자가검증 루프), 계획 수립, 동시성 제어
    scan/            OWASP Dependency-Check / Trivy 스캔 + 병합
    versioning/      출력 아티팩트 버전(`versions:set`) 적용
    reporting/       최종 리포트/diff 생성
    handoff/         AI 인수인계 가이드 생성
    streaming/       SSE 이벤트 버스 + 영속화
    models/          SQLAlchemy 모델 (Job, JobEvent)
    schemas/         Pydantic 요청/응답 스키마
    prereqs.py       로컬 실행 사전 준비 점검 (java/mvn/git/python/trivy)
    procenv.py       서브프로세스 환경변수(프록시)·실행파일 resolve 공통 로직
    config.py        Settings(.env 로딩), LangSmith 환경변수 브릿지
    logging_conf.py  표준 logging 설정, 서드파티 라이브러리(httpx/httpcore/openai) 기본 INFO 로그 억제
    main.py          FastAPI 앱 조립
  scripts/         check_prereqs.py(사전 준비 점검 CLI) + check-prereqs.ps1/.sh(진입점 래퍼)
  tests/           unit/(항상 실행) + integration/ — 후자는 마커 없는 API 레벨 테스트(기본 실행), `slow`(실제 mvn·git·java 필요), `external`(네트워크·시크릿 필요) 마커 테스트가 섞여 있고 기본 `pytest`는 `slow`/`external` 둘 다 제외(`addopts`)

frontend/    정적 HTML/CSS/JS — 빌드 단계 없음, 백엔드와 별도 배포
  index.html (새 작업 제출 폼 + Stage 0/1/2 LangGraph 개요 — 정적 콘텐츠, JS 진행상황 뷰 없음)
  history.html (이력), job.html (상세, 진행상황/분석/결과물 뷰), files.html (파일별 diff)
  assets/common.js (연결 설정 + 설정 모달), assets/job-view.js (진행상황/분석/결과물, job.html 전용)
  assets/app.js (index.html 전용 — 제출 후 job.html로 이동), assets/history.js, assets/job.js, assets/files.js, assets/app.css
  assets/vendor/ (jQuery/jsTree 로컬 번들 — CDN 미사용)

data/        참고용 사내 Maven 저장소 ZIP (도구가 다루는 입력 예시, 도구 코드 아님)
draft/       브레인스토밍 초안 (인입 파이프라인 원안, .env 템플릿 원본)
docs/        설계 스펙 + 이 아키텍처 문서
```

## 3. 전체 구조

백엔드(FastAPI)와 프론트엔드(정적 HTML)는 **분리된 배포 단위**다. 프론트엔드는 REST/SSE로만 백엔드와 통신하는 순수 클라이언트이며, 브라우저 `localStorage`에 API 서버 주소와 토큰을 저장한다.

```mermaid
flowchart LR
    subgraph Browser["개발자 브라우저"]
        FE["frontend/ (정적 HTML/JS) index.html + app.js"]
    end

    subgraph Backend["backend/ (FastAPI, 로컬 프로세스)"]
        API["api/routers/jobs, api/routers/artifacts, api/routers/health"]
        JM["orchestration/concurrency JobManager (세마포어, MAX_CONCURRENT_REPOS)"]
        PIPE["orchestration/pipeline run_pipeline (백그라운드 태스크)"]
        DB[("SQLite ▦Job, ▦JobEvent")]
        BUS["streaming/bus 프로세스 내 pub/sub"]
    end

    subgraph External["로컬 PATH의 외부 도구"]
        GIT["git"]
        MVN["mvn (+ OpenRewrite, Versions Plugin, Dependency-Check 플러그인)"]
        TRIVY["trivy"]
    end

    subgraph SaaS["외부 SaaS (인터넷 필요)"]
        NVD["NVD API"]
        OPENAI["OpenAI API"]
        LS["LangSmith (선택)"]
    end

    FE -- "REST: POST /jobs, GET /jobs/{id}/artifacts/*" --> API
    FE -- "SSE: GET /jobs/{id}/events" --> API
    API --> JM --> PIPE
    PIPE --> DB
    PIPE -- emit_event --> BUS
    BUS -- 실시간 스트림 --> API
    PIPE --> GIT
    PIPE --> MVN
    PIPE --> TRIVY
    MVN --> NVD
    PIPE -- "AI-fix 노드" --> OPENAI
    PIPE -.LangSmith 트레이싱(선택).-> LS
```

## 4. 요청 흐름: Job 생성부터 종료까지

`POST /jobs`([`backend/app/api/routers/jobs.py`](../backend/app/api/routers/jobs.py))는 Job 행을 만들고 `JobManager`([`orchestration/concurrency.py`](../backend/app/orchestration/concurrency.py))에 파이프라인을 예약한 뒤 **즉시 202를 반환**한다. 실제 동시 실행 여부는 세마포어(`MAX_CONCURRENT_REPOS`, 기본 3)가 결정하며, 초과분은 큐에서 대기한다 — 여러 사용자를 나누기 위한 값이 아니라 로컬 머신이 동시에 여러 `mvn` 빌드를 못 버티는 것을 막는 안전장치다.

전체 실행은 두 함수로 나뉜다: `run_pipeline`은 인입과 Stage 0(§4.1)만 하고 사람의 확인을 기다리며 멈추고, 확인이 들어오면 `run_pipeline_resume_after_version_confirm`이 나머지(출력 버전 적용 → 1단계 → 2단계 → 산출물)를 이어서 실행한다. `output_version`은 더 이상 `POST /jobs` 요청 인자가 아니다 — Stage 0가 항상 자동으로 계산해서 제안하고, 사람이 확인해야 값이 정해진다(§4.1).

```mermaid
sequenceDiagram
    participant FE as frontend
    participant API as POST /jobs
    participant JM as JobManager
    participant PIPE as run_pipeline
    participant DB as SQLite (Job/JobEvent)
    participant SSE as GET /jobs/{id}/events
    participant CONFIRM as POST /jobs/{id}/confirm-version
    participant RESUME as run_pipeline_resume_after_version_confirm

    FE->>API: multipart(git_url 또는 zip_file, run_stage1, run_stage2)
    API->>DB: Job(status=queued) 저장
    API->>JM: start(job_id, run_pipeline)
    API-->>FE: 202 {job_id, status: queued}
    FE->>SSE: EventSource 연결 (job_id)

    JM->>PIPE: 세마포어 확보 후 실행
    PIPE->>DB: status=running + emit("status")
    PIPE->>PIPE: ingest → Stage 0(§4.1: mvn effective-pom 분석 + 버전 제안 + 베이스라인 스캔)
    PIPE-->>SSE: 진행 중 log/status 이벤트 (DB에도 JobEvent로 영속)

    alt 1·2단계 둘 다 미선택
        PIPE->>DB: status=success (Stage 0 생략, 곧바로 종료)
    else 하나 이상 선택
        PIPE->>DB: status=awaiting_version_approval + current/suggested_version
        Note over PIPE,FE: run_pipeline은 여기서 리턴 — 살아있는 Task 없음
        FE->>CONFIRM: {output_version} (사람이 확인)
        CONFIRM->>RESUME: 백그라운드 스케줄링
        RESUME->>DB: status=running + emit("status")
        RESUME->>RESUME: 출력 버전 적용 → Stage1 → Stage2 → diff/report
        RESUME-->>SSE: 진행 중 log/status 이벤트 (DB에도 JobEvent로 영속)
        RESUME->>DB: status=success|stage1_needs_handoff|stage2_needs_handoff|failed, report_markdown 저장
    end
    SSE-->>FE: 최종 status 이벤트 후 연결 종료
    FE->>API: GET /jobs/{id}/artifacts → diff/report/handoff 목록 조회
```

`run_pipeline`([`orchestration/pipeline.py`](../backend/app/orchestration/pipeline.py))의 순서:

1. **인입** (`ingest/workspace.ingest`) — `source/` 확정, Maven 프로젝트 감지, `work/`에 baseline git 커밋 생성.
2. 1·2단계 둘 다 미선택이면 여기서 바로 `success`로 끝난다(버전을 정하거나 스캔할 이유가 없음).
3. **Stage 0** (§4.1) — 여기서 멈추고 사람의 확인을 기다린다.

확인 후 `run_pipeline_resume_after_version_confirm`이 이어받는 순서:

1. **출력 버전 적용** (`versioning/artifact_version.apply_output_version`) — `mvn versions:set` 실행 후 자체 체크포인트 커밋. Stage 0가 이미 만들어둔 `effective-pom.xml`을 재사용해 스택 정보를 다시 읽는다(재분석 안 함).
2. **1단계** (`run_stage1`이 true인 경우, `orchestration/multi_step.run_stage1_migration`) — 마이그레이션 계획 수립 → 단계별 그래프 실행. 끝나면(성공/노갭/인수인계 여부와 무관) 항상 재스캔해 `vulnerabilities_post_stage1` 이벤트로 얼마나 해소됐는지 보여준다(§7.5) — 2단계가 선택됐다면 이 재스캔 결과를 그대로 2단계 패치 대상으로 재사용한다(중복 스캔 없음).
3. **2단계** (`run_stage2`이 true인 경우, `orchestration/stage2_loop.run_stage2_patches`) — CVE별 패치 그래프 실행. **단, 1단계가 `needs_handoff`로 끝났다면 여기서 바로 실행하지 않고 `awaiting_approval`에서 멈춘다 — §7.4 참고.** 1단계를 안 돌렸다면(2단계만 선택) Stage 0의 베이스라인 스캔 결과를 재사용한다(§4.1) — work/가 그 이후 안 바뀌었기 때문. 패치가 끝나면 다시 최종 스캔을 돌려 `vulnerabilities_final` 이벤트로 남은 취약점을 보여준다.
4. **산출물 작성** — `git diff baseline..HEAD`로 `output/patch.diff`, 단계별 리포트를 이어붙인 `output/report.md`, 막힌 단계가 있으면 `output/handoff/*.md`.
5. Job 상태를 `success` / `stage1_needs_handoff` / `stage2_needs_handoff` / `failed`(또는 3번의 예외 상황이면 `awaiting_approval`)로 확정 — 어느 단계가 막았는지 상태값 자체가 구분한다(§7.4).

`IngestError`는 `failed`로, 그 외 모든 예외도 `except Exception`으로 잡아 `failed`로 처리한다 — 개별 job의 실패가 서버 프로세스 전체를 죽이지 않도록 하는 것이 목적이다(주석 원문: "a job failure must never crash the server process"). 이 예외 처리는 `run_pipeline`과 `run_pipeline_resume_after_version_confirm` 양쪽 모두에 동일하게 있다.

### 4.1 Stage 0 — 버전 확인 + 베이스라인 스캔 게이트

설계 배경: [`docs/superpowers/specs/2026-08-10-stage0-version-scan-restructure-design.md`](superpowers/specs/2026-08-10-stage0-version-scan-restructure-design.md).

1·2단계 중 하나라도 선택되면, 실제 마이그레이션/패치에 들어가기 전에 항상 이 게이트를 거친다:

1. `mvn effective-pom`으로 현재 버전/스택을 분석(`inventory` 이벤트).
2. `versioning/artifact_version.compute_stage0_output_version`으로 출력 버전을 자동 제안 — **1단계가 선택됐으면 MAJOR, 아니면 MINOR**를 증가시킨다(스택이 이미 목표와 같아도 마찬가지). 감지된 현재 버전이 `MAJOR.MINOR.PATCH` 형태가 아니면 정규화만 하고 그대로 반환.
3. 마이그레이션 전 베이스라인 취약점 스캔(`vulnerabilities_baseline` 이벤트, §8.1과 동일한 `run_combined_scan`).
4. Job 상태를 `awaiting_version_approval`로 바꾸고 `current_version`/`suggested_version`을 `status` 이벤트에 실어 멈춘다 — **살아있는 백그라운드 Task 없이 리턴**(§10의 `awaiting_approval`과 같은 설계).

사람이 `POST /jobs/{id}/confirm-version`으로 값을 확인해야 다음 단계로 넘어간다:

- **확인값이 현재 버전과 같으면 409** — 동일 버전으로는 절대 진행할 수 없다(사내 Nexus 배포 시 기존 아티팩트를 덮어쓰지 못하게 하려는 의도적인 정책, 자동 제안값을 그대로 안 써도 됨).
- Job이 `awaiting_version_approval`이 아니면 409, 존재하지 않으면 404.
- 통과하면 `run_pipeline_resume_after_version_confirm`을 새 백그라운드 Task로 스케줄링한다(§4의 순서로 이어짐).

`awaiting_version_approval`은 `awaiting_approval`(§7.4)과 마찬가지로 의도적으로 터미널 상태가 아니다(`models/job.py`의 `TERMINAL_JOB_STATUSES`에 없음) — `POST /jobs/{id}/cancel`이 이 상태를 살아있는 Task 없이 바로 `cancelled`로 마감할 수 있는 것도 같은 이유(§7.4의 `_finalize_cancelled` 경로 재사용).

### 4.2 사내 parent POM(BOM 겸용) 목표 버전 전이

설계 배경: [`docs/superpowers/specs/2026-08-11-internal-parent-pom-target-version-design.md`](superpowers/specs/2026-08-11-internal-parent-pom-target-version-design.md).

`mvn effective-pom`으로 뽑은 스택 버전(§4.1의 1번)은 상속 체인까지 반영된 최종 병합 결과라, 프로젝트의 루트 `pom.xml`이 사내 공용 parent POM(BOM 겸용, 예: `ace-parent`)을 상속하고 있으면 그 parent가 정의한 값이 이미 녹아 있다. 이 경우 Stage 1이 이 프로젝트 자신의 파일만 아무리 고쳐도 목표 스택에 도달할 수 없다 — parent 자체의 새 버전을 가리키는 것 말고는 방법이 없다.

- **감지**(`ingest/maven_detect.detect_external_parent`): 프로젝트 자신의 원본(effective 아님) `pom.xml`의 `<parent>`가 알려진 공개 parent(`_PUBLIC_PARENT_ALLOWLIST`, 현재는 `spring-boot-starter-parent`만) 목록에 없으면 "사내 parent POM일 수 있다"고 판단한다. `run_pipeline`이 Stage 0의 `inventory` 이벤트(분석 패널)와 `awaiting_version_approval`의 `status` 이벤트(`detected_parent` 필드) 양쪽에 이 결과를 실어 보낸다.
- **입력**: 확인 패널에 "사내 parent POM 목표 버전" 입력창이 조건부로 뜬다(감지됐을 때만). `POST /jobs/{id}/confirm-version`의 `parent_target_version`으로 전달 — 비워두면(기본) 이 프로젝트만 마이그레이션하고 parent는 그대로 둔다. 감지된 현재 parent 버전과 같은 값을 넣으면 409(출력 버전과 동일한 정책).
- **적용**(Stage 1, `orchestration/multi_step.run_stage1_migration`): `parent_target_version`이 있으면 계획의 맨 앞에 `parent_pom` 스텝을 하나 끼워 넣는다 — OpenRewrite 레시피가 아니라 `mvnrewrite/parent_patch.patch_parent_version`(XML `<parent><version>` 텍스트를 직접 교체하는 mechanical 패치, `mvn versions:update-parent`가 지정한 값보다 높은 버전으로 새는 걸 실측으로 확인해 직접 XML 조작으로 바꿨다)로 처리하지만, 검증/커밋/실패 시 인수인계 가이드는 다른 스텝과 동일한 Stage 1 그래프(§7.2)를 그대로 탄다. 이 스텝이 성공하면 `mvn effective-pom`을 다시 돌려 재분석하고(새 parent 버전이 가져온 스택으로), 그 값으로 나머지 계획(`build_migration_plan`)을 다시 세운다 — 막혔던 지점을 저장해두지 않고, 재분석된 현재 상태가 자연스럽게 이미 끝난 스텝을 계획에서 빼준다(§7.6의 인수인계 후 재개가 재사용하는 것과 같은 패턴).
- **목표 버전을 안 준 경우**: parent가 감지됐는데도 `parent_target_version`을 비워뒀다면, 1단계가 끝난 뒤 리포트에 "이 프로젝트만으로는 목표에 도달할 수 없습니다" 안내 문구가 남는다(`run_pipeline_resume_after_version_confirm`).

## 5. 인입 파이프라인 (`ingest/`, `checkpoint/`)

설계 스펙의 인입 다이어그램을 그대로 구현한다.
- **Git 인입** ([`git_source.py`](../backend/app/ingest/git_source.py)): `git clone --depth 1 [--branch {ref}]`. 타임아웃은 `BUILD_TIMEOUT_SECONDS` 공유.
- **ZIP 인입** ([`zip_source.py`](../backend/app/ingest/zip_source.py)): 압축 해제 **전에** 업로드 크기 / 해제 후 예상 크기 / 파일 개수를 먼저 검사해 초과 시 바이트 하나도 쓰지 않고 거부. 각 zip 엔트리는 `resolve()` 후 목적지 디렉토리 밖으로 벗어나는지 검사(경로 traversal 방지). 이후 GitHub 스타일 `repo-main/...` 단일 최상위 폴더를 한 겹 벗겨낸다(`unwrap_single_top_level`).
- **Maven 감지** ([`maven_detect.py`](../backend/app/ingest/maven_detect.py)): root `pom.xml` 존재 확인 → 없으면 `build.gradle*`/`settings.gradle*` 존재 여부로 Gradle 프로젝트임을 구분해 명시적으로 범위 외 에러. `packaging=pom`이면 `<modules>`를 1단계 깊이까지만 수집(중첩 멀티모듈은 미지원, 참고 저장소 4개가 모두 1단계 구조라 이렇게 정함).
- **work/ 준비** ([`workspace.materialize_work_from_source`](../backend/app/ingest/workspace.py)): `source/`를 `.git` 제외하고 `work/`로 복사한 뒤 `work/`에서 새로 `git init` + baseline 커밋(`checkpoint/git_repo.git_init_and_baseline_commit`). 원본 Git 히스토리를 상속하지 않고, 이 도구가 만든 변경만 담긴 최소 히스토리를 새로 시작한다.

작업 디렉토리는 `{JOBS_DATA_DIR}/{job_id}/{source,work,output}/`로 분리되며, `source/`는 절대 수정되지 않는다.

## 6. 체크포인트/롤백 (`checkpoint/git_repo.py`)

`work/`를 그 자체로 하나의 git 저장소로 취급한다.

- 커밋 author는 개발자 본인이 아니라 도구의 봇 아이덴티티(`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`, 기본 `upgrade-agent`)로 고정.
- 검증(빌드/테스트)을 통과한 단계마다 `commit_checkpoint`로 체크포인트 커밋(빈 변경도 `--allow-empty`로 허용 — 레시피가 아무것도 바꾸지 않은 것도 유효한 성공).
- 재시도 한도 소진 시 `reset_to_checkpoint`가 `git reset --hard` + `git clean -fd`로 마지막 체크포인트까지 되돌린다.
- 최종 산출물 diff는 `diff_since(baseline, HEAD)` 한 줄로 계산된다 — 실패한 시도는 애초에 커밋되지 않으므로 별도 필터링 없이 "검증된 변경만" 포함된다.
- `changed_file_count`는 AI 수정이 손댄 파일 수를 세어 "자동 적용 범위 제한" 게이트에 쓰인다.

## 7. Stage 1 — 스택 마이그레이션 (`orchestration/planning.py`, `graph_stage1.py`, `multi_step.py`)

### 7.1 계획 수립 (`planning.build_migration_plan`)

순수 함수(I/O 없음). 감지된 버전과 목표 버전 사이의 단계를 `mvnrewrite/recipe_catalog.yaml`에서 조회해 순서를 만든다.

- Java 업그레이드는 있다면 항상 맨 앞.
- Spring Boot는 카탈로그에 있는 한 홉씩만 전진(예: 2.7→3.0→3.2→3.4→3.5→4.0). 카탈로그에 다음 홉이 없으면, 그 지점부터 목표 버전까지를 **레시피 없이 AI가 직접 시도하는 스텝 하나**(`recipe=None`)로 채우고 계획을 마무리한다 — 더 이상 계획에서 조용히 빠지지 않는다(§7.2가 이 스텝을 어떻게 실행하는지 설명). **실제 사례**: 4.0→4.1은 `recipe_catalog.yaml`에 항목이 없다 — 2026-08-07 실측(job #7) + 웹 조사로 확인한 결과, `org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_1`은 존재하지 않고(무료 카탈로그 `org.openrewrite.recipe:rewrite-spring`), 완전한 버전은 `io.moderne.java.spring.boot4.UpgradeSpringBoot_4_1`로 존재하지만 **Moderne 유료 구독 전용**(Moderne Proprietary License)이다. 무료 쪽엔 프로퍼티 키만 바꿔주는 `SpringBootProperties_4_1`가 있지만 Boot 버전 자체는 안 올리고 이마저도 별도 라이선스(Moderne Source Available License)라 지금 방식(Maven Central `RELEASE` 직접 사용)과 안 맞는다. 자세한 내용은 `recipe_catalog.yaml`의 해당 주석 참고. Java/Spring AI 차원에 카탈로그 갭이 있을 때도 동일하게 처리한다.
- Spring Cloud는 별도 스텝이 아니라, 프로젝트가 Spring Cloud를 쓰는 경우(`detected.spring_cloud_version is not None`) 목표 Boot 버전에 대응하는 Cloud 트레인을 그 Boot 스텝에 실어 보낸다(`spring_cloud_trains` 매핑, Boot 4.1 → Cloud 2025.1).
- Spring AI는 프로젝트가 쓰는 경우에만, **Spring Boot가 목표(4.x)까지 전부 끝난 뒤 맨 마지막에** 한 번 삽입한다 — Boot가 처음 4.x에 도달하는 스텝 직후가 아니라 의도적으로 맨 뒤다: 4.0→4.1처럼 Boot 쪽에 아직 카탈로그 갭 스텝이 남아있을 수 있는데, §7.3의 순차 실행이 실패한 스텝에서 그 자리에 멈추는 구조라 Spring AI 실패가 더 근본적인 Boot 목표 도달을 막아버리면 안 되기 때문(2026-08-09, job #10 계기로 순서 재검토). 이미 Boot가 목표 버전이라 Boot 스텝 자체가 없는 프로젝트도 Spring AI만 남았다면 스텝이 잡힌다. **실제 사례**: 2.0 레시피는 공식 `org.openrewrite.recipe:rewrite-spring`에 없고, 대신 Arconia Migrations(`io.arconia.migrations:rewrite-spring`, Apache License 2.0)의 `UpgradeSpringAi_2_0`를 쓴다 — 이 카탈로그 최초의 서드파티(공식 OpenRewrite 밖) 레시피라, 그런 스텝은 화면/로그/리포트에 표시되는 설명에 `(서드파티 레시피)`가 자동으로 붙는다(`orchestration/planning.py`). 검증 근거는 `recipe_catalog.yaml`의 해당 주석 참고.

### 7.2 단일 스텝 자가검증 루프 (`graph_stage1.build_stage1_graph`, LangGraph)

```mermaid
stateDiagram-v2
    [*] --> plan
    plan --> apply: 다음 레시피 있음
    plan --> ai_fix: 카탈로그에 알려진 레시피 없음
    plan --> [*]: 이미 목표 버전
    apply --> verify: 레시피 적용 성공(exit=0)
    apply --> ai_fix: 레시피 적용 자체가 실패(exit≠0)
    verify --> [*]: mvn test-compile 성공
    verify --> ai_fix: 실패, 재시도 여유 있음
    verify --> handoff: 실패, 재시도 한도 소진
    ai_fix --> verify: 변경 파일 수 ≤ 한도
    ai_fix --> handoff: 변경 파일 수 > 한도
    handoff --> [*]
```

- `apply`: `mvnrewrite/rewrite_client.run_openrewrite_recipes`가 `org.openrewrite.maven:rewrite-maven-plugin:RELEASE`를 좌표로 직접 호출한다(대상 프로젝트의 `pom.xml`에 플러그인 설정을 주입하지 않음 — 주입하면 그 변경 자체가 diff에 오염되어 매번 되돌려야 하는 문제가 생기기 때문). 카탈로그에 레시피가 없는 스텝은 이 노드를 건너뛰고 곧장 `ai_fix`로 간다 — 적용할 레시피 자체가 없기 때문. **레시피 실행 자체가 실패(exit≠0)하면 `verify`를 거치지 않고 곧장 `ai_fix`로 간다**(2026-08-09, job #11 계기) — `verify`로 보냈다가는 아무것도 안 바뀐 채 우연히 컴파일이 통과해 조용히 "성공"으로 끝나버리거나, 원래 있던 실패 원인이 `verify`의 결과로 덮어써져 사라질 수 있기 때문. 자세한 배경은 `docs/superpowers/specs/2026-08-09-stage1-apply-verify-integrity-design.md` 참고.
- `verify`: `mvn test-compile`(운영+테스트 소스 컴파일, 테스트 실행은 안 함) — 원래 `mvn compile`(운영 소스만)이었으나, 레시피가 테스트 코드만 깨뜨리고 넘어가는 걸 못 잡는 문제가 있어 확장(같은 계기). 테스트를 실제로 돌리진 않아 부작용(예: 실제 메일 전송을 시도하는 테스트) 위험은 없다.
- `ai_fix`: `langchain.agents.create_agent` + `ChatOpenAI`(`orchestration/llm.get_chat_model`) + `orchestration/tools.build_tools`가 제공하는 `read_file`/`edit_file`/`run_build`/`run_recipe`/`list_available_recipes` 툴로 스스로 고친다. 두 가지 경우에 호출된다: (1) 레시피 적용 후 `verify`가 실패했을 때, 빌드 에러를 고쳐 달라고 요청 — 기존 동작. (2) 레시피가 아예 없을 때(`plan`에서 곧장 옴, 첫 시도), 목표 버전까지 직접 올려 달라고 요청 — 이후 재시도는 (1)과 동일하게 "아직도 컴파일이 안 된다"는 빌드 출력을 주고 계속 고치게 한다. 모든 파일 접근은 `work_dir` 밖으로 나가지 못하도록 경로를 검증한다(`_safe_path`). 호출 하나하나는 `orchestration/callbacks.LocalLLMLogger`가 `output/logs/{stage}/llm/*.md`으로 로컬에도 남긴다(LangSmith 트레이싱과 별도, LangSmith 접근 권한이 없는 사람도 job 폴더만으로 무슨 일이 있었는지 볼 수 있게).
- 재시도 상한 `COMPILE_FIX_MAX_ATTEMPTS`(기본 2), 자동 적용 파일 수 상한 `COMPILE_FIX_AUTO_APPLY_MAX_FILES`(기본 3) — 두 값 모두 `.env`로 조정. 레시피 없이 처음부터 AI가 버전을 올리는 스텝은 파일 수 상한만 별도로 `COMPILE_FIX_AUTO_APPLY_MAX_FILES_NO_RECIPE`(기본 20)를 쓴다 — 컴파일 에러 하나 고치는 것보다 자연스럽게 훨씬 많은 파일(설정 클래스, import, deprecated API 사용처...)을 건드리기 때문.

### 7.3 외부 루프 (`multi_step.run_stage1_migration`)

계획의 각 스텝을 순서대로 실행. 성공하면 체크포인트 커밋 후 다음 스텝, 실패(`needs_handoff`)하면 마지막 체크포인트로 롤백하고 `handoff/guide_builder.build_handoff_guide`로 가이드를 만든 뒤 **그 자리에서 멈춘다**(뒤 스텝은 앞 스텝이 성공했다는 전제이므로 무리하게 진행하지 않음). 레시피 없는 스텝이 실패한 경우도 동일한 경로를 탄다 — `apply`가 애초에 실행되지 않았을 뿐, 성공/실패 처리는 다른 스텝과 다르지 않다.

### 7.4 1단계가 막혔을 때 2단계 진입 게이트 (HITL 승인)

1단계가 `stage1_needs_handoff`로 끝났는데 2단계(취약점 스캔/패치)도 요청된 상태라면, `run_pipeline_resume_after_version_confirm`은 2단계를 곧장 이어서 실행하지 않는다 — Job 상태를 `awaiting_approval`로 남기고 멈춘다(그때까지의 diff/report는 저장됨). 1단계의 미해결 갭은 2단계가 그 위에서 실행된다고 사라지는 게 아니기 때문에, 사람이 명시적으로 계속 진행할지 판단하게 한다.

Job의 최종 `needs_handoff` 계열 상태는 `stage1_needs_handoff`/`stage2_needs_handoff` 둘로 나뉜다(스펙: `docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md`) — 어느 단계가 실제로 막았는지 상태값만 보고 알 수 있게 하기 위해서다(예전엔 하나의 `needs_handoff`였고, `output/handoff/` 안 파일명을 사람이 직접 봐야 구분됐다). 아래는 Job 상태 전체 흐름이다 — Stage 0(§4.1)까지 포함해 `queued`부터 터미널 상태까지 한 번에 보여준다:

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> success: 1·2단계 미선택\n(Stage 0 생략)
    running --> awaiting_version_approval: 1·2단계 중 하나 이상\n(Stage 0 완료)
    awaiting_version_approval --> running: POST /jobs/{id}/confirm-version
    running --> awaiting_approval: 1단계 stage1_needs_handoff\n+ 2단계 요청됨
    running --> success
    running --> stage1_needs_handoff: 1단계 handoff\n(2단계 미요청 또는\n2단계도 성공)
    running --> stage2_needs_handoff: 2단계에서 handoff\n(1단계는 성공 또는 미요청)
    running --> failed
    awaiting_approval --> running: POST /jobs/{id}/proceed
    running --> stage2_needs_handoff: 2단계 재개, 2단계도 handoff
    running --> stage1_needs_handoff: 2단계 재개, 2단계는 성공\n(1단계 갭은 여전히 남음)
    stage1_needs_handoff --> running: POST /jobs/{id}/resume-stage1\n(§7.6)
```

- `POST /jobs/{id}/proceed`(§11)가 `run_pipeline_resume_stage2`를 백그라운드로 스케줄링한다. `work_dir`/`output_dir`는 `job_id`로부터 결정적으로 재계산되고(`{JOBS_DATA_DIR}/{job_id}/{work,output}`), 지역변수로만 들고 있던 baseline 커밋은 `checkpoint/git_repo.resolve_ingest_baseline`/`resolve_stage_baseline`이 git 히스토리(커밋 순서: ingest baseline → 출력 버전 체크포인트 → 1단계...)에서 되찾는다 — 새 DB 컬럼 없이 재개 가능.
- `awaiting_approval`과 `awaiting_version_approval` 둘 다 의도적으로 "터미널 상태"(`models/job.py`의 `TERMINAL_JOB_STATUSES`)에 넣지 않는다 — 그 덕분에 이미 열려 있는 SSE 연결(§10)이 승인/확인 후 이어지는 이벤트를 재연결 없이 그대로 받는다.
- `/proceed`로 재개된 2단계가 스스로도 handoff가 필요해지면 최종 상태는 `stage2_needs_handoff`(더 새롭고 실행 가능한 문제이므로) — 2단계가 문제 없이 끝나면 1단계의 원래 갭이 여전히 안 풀린 것이므로 `stage1_needs_handoff`로 되돌아간다. 두 값 모두 특별한 새 값(예: "둘 다 막힘")을 만들지 않는다 — 1단계 문제는 `/proceed`를 누른 시점에 사람이 이미 인지한 것이고, `output/handoff/stage1-guide.md`도 그대로 남아 필요하면 볼 수 있다.

### 7.5 1단계 이후 재스캔

1단계가 끝나면(성공/노갭/인수인계 여부와 무관, `run_pipeline_resume_after_version_confirm`) 항상 `run_combined_scan`을 다시 돌려 `vulnerabilities_post_stage1` 이벤트로 내보낸다 — "1단계만으로 취약점이 얼마나 해소됐는지"를 2단계 선택 여부와 무관하게 보여주기 위함이다(예전에는 2단계를 선택했을 때만, 2단계 패치 대상을 뽑는 부산물로만 실행됐다). 2단계도 선택된 경우 이 스캔 결과를 그대로 2단계 패치 대상으로 재사용하므로 스캔이 두 번 도는 일은 없다. 1단계가 `stage1_needs_handoff`로 끝나 §7.4의 승인 대기에 들어간 경우, 나중에 `POST /jobs/{id}/proceed`로 재개하는 `run_pipeline_resume_stage2`는 이 결과를 다시 읽어서(`_latest_event_data`) 쓴다 — work/가 그 사이 안 바뀌었으므로 재스캔하지 않는다.

### 7.6 1단계 인수인계 후 재개 (`POST /jobs/{id}/resume-stage1`)

설계 배경: [`docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md`](superpowers/specs/2026-08-11-stage1-handoff-resume-design.md).

`stage1_needs_handoff`로 끝난 job은 `work/`를 사람이 외부 AI 코딩 도구 등으로 직접 고친 뒤, 화면에서 "인수인계 후 재개" 버튼으로 이어서 진행할 수 있다. 게이트는 `job.status == "stage1_needs_handoff"` 하나뿐이다 — `run_stage2` 값은 확인하지 않는다: `stage1_needs_handoff`는 "더 이상 자동으로 진행될 게 없을 때"만 붙는 최종 상태라, 2단계가 원래 요청됐던 job이라도 이 상태에 도달했다는 것 자체가 이미 2단계가 없었거나 끝까지 성공했다는 뜻이기 때문(§7.4의 두 번째 다이어그램 분기 참고).

`run_pipeline_resume_stage1_after_handoff`(`orchestration/pipeline.py`)의 순서:

1. **검증** (`orchestration/multi_step.verify_after_manual_fix`) — `mvn test-compile` 한 번만 실행한다. AI 재시도는 하지 않는다 — 사람이 직접 고친 결과를 확인만 하는 동작이라, AI를 다시 태우면 사람의 의도와 다르게 또 고칠 위험이 있다(job #44에서 AI가 원래 맞았던 import를 오히려 틀리게 고친 사례가 실제로 있었다 — `docs/lessons-learned/2026-08-11-jackson3-objectmapper-migration.md`).
2. **검증 실패** → 아무것도 커밋하지 않고 `stage1_needs_handoff`로 되돌아간다. `output/handoff/stage1-guide.md`를 최신 빌드 출력으로 덮어써서 재시도할 수 있게 한다.
3. **검증 성공** → 체크포인트 커밋 후 `mvn effective-pom`을 다시 돌려 현재 `work/`의 실제 스택을 재분석하고(사내 parent POM 기능, §4.2와 동일한 재분석 패턴), `run_stage1_migration`을 그 값으로 다시 호출해 나머지 계획을 이어서 실행한다 — 막혔던 스텝이 몇 번째였는지는 어디에도 저장하지 않는다. 재분석된 버전이 이미 반영된 수정을 나타내므로 `build_migration_plan`이 자연스럽게 다음 스텝부터 계획을 세운다.
4. 재개가 끝까지 성공하면 1차 시도 때 남은 `output/handoff/stage1-guide.md`를 지운다 — 안 지우면 성공한 job인데도 "아직 인수인계 필요"라는 낡은 파일이 결과물 목록에 남는다. 또 막히면 새 가이드로 덮어쓰고 다시 `stage1_needs_handoff`(반복 가능).

`report_markdown`은 1차 시도 리포트 뒤에 이번 재개 결과를 이어붙인다(`run_pipeline_resume_stage2`와 동일한 패턴).

## 8. Stage 2 — 개별 CVE 패치 (`scan/`, `graph_stage2.py`, `stage2_loop.py`)

### 8.1 스캔 + 병합 (`scan/combined.run_combined_scan`)

OWASP Dependency-Check(`scan/dependency_check.py`)와 Trivy(`scan/trivy.py`)를 `asyncio.gather`로 병렬 실행 후 `scan/merge.py`에서:

- Dependency-Check는 PURL(`pkg:maven/group/artifact@version`)을, Trivy는 `group:artifact` 형식을 쓰므로 동일 형식으로 정규화.
- `(cve_id, package)` 키로 중복 제거, 더 정보가 많은 쪽(fix_version 보유 또는 더 높은 CVSS)을 채택.
- Trivy가 여러 수정 버전을 콤마로 보고하면(`pick_fix_version`), 불필요한 메이저 업그레이드를 피하기 위해 **현재 major.minor 라인 안에서 가장 작은 버전**을 우선 선택.
- `FAIL_ON_CVSS`(기본 7.0) 미만은 걸러낸다.

Dependency-Check 구현상 주의점(코드 주석에 명시): 리액터 모듈이 서로 의존하면 `dependency-check:check`만 단독 실행 시 실패하므로 같은 명령에 `install`을 먼저 넣는다. `-DoutputDirectory`는 모듈별로 무시되므로 `**/target/dependency-check-report.json`을 전부 글롭으로 찾아 합친다.

### 8.2 CVE별 패치 루프 (`graph_stage2.py`)

Stage 1과 같은 모양(apply → verify → ai_fix → handoff)이지만:

- `apply`는 `mvnrewrite/dependency_patch.patch_dependency_version` — 의존성이 `${property}`로 선언돼 있으면 `versions:set-property`, 리터럴 버전이면 `versions:use-dep-version`(리액터 전체 인지)을 선택해 사용한다. Maven Versions Plugin의 `versions:use-dep-version`이 `${property}` 참조 의존성은 조용히 건너뛴다는 점을 실측으로 확인하고 분기한 것.
- `verify`는 `mvn verify`(컴파일+테스트)만 확인한다. 스펙의 "Dependency-Check/Trivy 재실행"은 CVE 하나마다 전체 스캔을 다시 돌리면 너무 느리므로, **배치 전체에 대해 실행 전/후 한 번씩**으로 대체 — 실행 전 목록은 §7.5(1단계 이후 재스캔)나 Stage 0 베이스라인(§4.1)을 재사용하고, 실행 후는 아래 §8.3의 최종 스캔이 맡는다. CVE 하나하나 단위의 재검증은 여전히 없다(빌드 검증만).

### 8.3 외부 루프 (`stage2_loop.run_stage2_patches`)

Stage 1과 달리 **CVE들은 서로 독립적**이므로 하나가 `needs_handoff`가 되어도 멈추지 않고 나머지를 계속 시도한다. 각 실패마다 개별 롤백 + 개별 handoff 가이드(`output/handoff/stage2-{cve}-guide.md`)가 생긴다. 패치 루프가 모두 끝나면(`orchestration/pipeline._run_stage2_block`) `run_combined_scan`을 한 번 더 돌려 `vulnerabilities_final` 이벤트로 남은 취약점을 보여준다 — Stage 1과 마찬가지로, 이 배치 전체가 취약점을 얼마나 해소했는지 확인하기 위함이다.

### 8.4 캐시 갱신 요청 처리 (NVD/Trivy)

2026-08-08 변경: 스캔(`run_trivy_scan`/`run_dependency_check`)은 더 이상 NVD/Trivy DB를 암묵적으로 갱신하지 않는다(`--skip-db-update --skip-java-db-update`, `-DautoUpdate=false`) — 사내망 프록시 환경에서 스캔 도중 예측 불가능하게 네트워크를 타다 지연/실패하는 걸 막기 위해서다. 그 대신 캐시는 오직 **사람이 명시적으로 요청한 갱신**으로만 최신화된다. **최초 실행 시 캐시가 비어 있으면 스캔이 사실상 아무 취약점도 못 찾으므로, 첫 job을 돌리기 전에 반드시 한 번 갱신을 눌러야 한다** (`backend/README.md`에도 안내).

`POST /cache/refresh`는 §4의 `POST /jobs`와 똑같은 패턴을 그대로 재사용한다 — 새 스트리밍 인프라를 만들지 않고, `Job` 테이블에 `source_type="cache_refresh"`인 행을 하나 만들어 기존 `JobManager`/`JobEvent`/`GET /jobs/{id}/events` SSE에 얹는다. "지금 갱신 중인가?"는 별도 in-memory 상태 없이 `Job` 테이블에서 `source_type="cache_refresh" AND status="running"`을 조회하면 된다. `list_jobs`(`GET /jobs`)는 이 행들을 걸러내므로 이력 화면(`history.html`)에는 나타나지 않는다 — 마이그레이션 job이 아니라 유틸리티 실행이기 때문이다. `work/`/`source/`/`output/` 같은 워크스페이스도 만들지 않는다(`ingest`를 타지 않음).

```mermaid
sequenceDiagram
    participant FE as frontend (설정 모달)
    participant API as POST /cache/refresh
    participant JM as JobManager
    participant RUN as run_cache_refresh
    participant DB as SQLite (Job/JobEvent)
    participant SSE as GET /jobs/{id}/events

    FE->>API: (인자 없음)
    API->>DB: Job(source_type=cache_refresh, status=queued) 저장
    API->>JM: start(job_id, run_cache_refresh)
    API-->>FE: 202 {job_id, status: queued}
    FE->>SSE: EventSource 연결 (job_id) — 아이콘 회전 시작

    JM->>RUN: 세마포어 확보 후 실행 (마이그레이션 job과 동일 풀 공유)
    RUN->>DB: status=running + emit("status")
    RUN->>RUN: Trivy DB 갱신 → Trivy Java DB 갱신 → Dependency-Check NVD 갱신(update-only 골)
    loop 각 단계
        RUN->>DB: JobEvent 기록 (log)
        RUN-->>SSE: bus.publish (실시간, 모달의 "현재 단계" 텍스트로 표시)
    end
    RUN->>DB: status=success|failed
    SSE-->>FE: 최종 status 이벤트 → 연결 종료, 아이콘 정지, 새 시각 재조회
```

- `orchestration/cache_status.read_cache_status`는 Job 이력과 무관하게 **파일에서 직접** 마지막 갱신 시각을 읽는다: Trivy는 캐시 디렉터리의 `db/metadata.json`에 실제로 `UpdatedAt`이 기록돼 있어 정확한 값을 쓰고, Dependency-Check는 이런 메타데이터가 없어 `odc.mv.db` 파일의 mtime으로 근사한다.
- `orchestration/cache_refresh.run_dependency_check_update_only`는 `org.owasp:dependency-check-maven:update-only` 골을 쓴다 — pom.xml이 없는 디렉터리에서 실행해도 Maven이 자동으로 "standalone-pom" 스텁을 만들어 정상 동작함을 실측 확인했다(별도 더미 pom.xml 불필요). 첫 전체 동기화가 30분 이상 걸릴 수 있어 `BUILD_TIMEOUT_SECONDS`(기본 900s) 대신 별도의 넉넉한 타임아웃(`NVD_UPDATE_TIMEOUT_SECONDS`, 3600s)을 쓴다.
- `orchestration/cache_refresh.run_trivy_db_refresh`는 `--download-db-only`와 `--download-java-db-only`를 **순차** 호출한다 — 둘을 한 번에 지정하면 trivy가 에러를 낸다(실측 확인).

## 9. 산출물 (`reporting/`, `handoff/`)

- `output/patch.diff` — `checkpoint/git_repo.diff_since(baseline, HEAD)`.
- `output/report.md` — Stage 1 리포트(`reporting/report_builder.build_report`: 진행된 단계, 자동 계획에서 제외된 항목, 막힌 지점)와 Stage 2 리포트(`stage2_loop._build_stage2_report`)를 `\n\n---\n\n`으로 이어붙인 것.
- `output/handoff/*.md` — `handoff/guide_builder.build_handoff_guide`가 **별도 LLM 호출 없이** 이미 그래프 상태에 있는 정보(실패한 스텝 설명, 사용한 메커니즘, AI가 시도한 tool call들, 마지막 빌드 출력)로 조립하는 마크다운. 다른 AI 코딩 도구에 그대로 붙여넣을 수 있는 형태.
- `output/logs/{stage}/llm/*.md` — LLM 호출별 로컬 로그(§7.2).
- `output/trivy/trivy-report.json` — Trivy 원본 결과. **고정된 한 경로**라 Stage 0 베이스라인/1단계 이후/2단계 이후, 스캔이 돌 때마다 덮어써진다 — 파일 자체는 항상 "가장 최근 스캔"만 담고 있고, 화면의 취약점 표들이 보여주는 각 시점(§4.1, §7.5, §8.3)의 스냅샷은 이 파일이 아니라 `JobEvent`(§10)에 별도로 영속화돼 있다.

## 10. Job 상태/진행 스트리밍 (`models/`, `streaming/`)

```mermaid
flowchart LR
    PIPE["run_pipeline"] -- "emit_event()" --> WRITE["JobEvent 행 저장\n(seq 채번)"]
    WRITE --> PUB["bus.publish()\n(JobEventBus, asyncio.Queue)"]
    PUB --> LIVE["살아있는 SSE 구독자에게 즉시 전달"]
    CLIENT["새로 연결한 클라이언트"] -- "GET /jobs/{id}/events" --> REPLAY["seq 순서로 과거 JobEvent 재생"]
    REPLAY --> LIVE2["이후 live 이벤트로 전환"]
```

- `Job`: 생성 입력(소스 타입/참조, 단계 실행 여부 — `output_version`은 더 이상 입력이 아니다), 상태(`queued`→`running`→(`awaiting_version_approval`→`running`→)(`awaiting_approval`→`running`→)`success`|`stage1_needs_handoff`|`stage2_needs_handoff`|`failed`), `output_version`(Stage 0 확인 후에야 채워짐), 최종 리포트를 보관. `awaiting_version_approval`은 Stage 0가 끝나고 출력 버전 확인을 기다리는 중간 상태(§4.1), `awaiting_approval`은 1단계가 막혔고 2단계도 요청된 job이 사람의 승인(`POST /jobs/{id}/proceed`, §7.4)을 기다리는 중간 상태 — 둘 다 의도적으로 터미널이 아니다. `stage1_needs_handoff`는 터미널이지만 `POST /jobs/{id}/resume-stage1`(§7.6)로 다시 `running`에 진입할 수 있다.
- `JobEvent`: 진행 타임라인의 각 항목(`log`/`status`)을 `seq` 순서로 영속화 — DB에 남기 때문에 job 종료 후에도, 또는 클라이언트가 중간에 재연결해도 히스토리를 그대로 재생할 수 있다.
- `streaming/sse.stream_job_events`는 **구독을 먼저 걸고 나서** 히스토리를 재생한다(순서를 반대로 하면 재생 쿼리와 구독 사이에 발행된 이벤트를 놓칠 수 있음). 재생된 `seq`는 집합에 담아두고, 이후 live 큐에서 같은 `seq`가 다시 오면 중복 전달을 걸러낸다.
- `JobEventBus`는 프로세스 내 `asyncio.Queue` 기반 pub/sub일 뿐 영속성이 없다 — 영속성은 전적으로 `JobEvent` 테이블이 담당하고, 버스는 "지금 열려 있는 SSE 연결에 실시간으로 밀어주는" 역할만 한다.

## 11. API 표면 (`api/routers/`)

모든 라우터는 `require_api_token`([`api/deps.py`](../backend/app/api/deps.py)) 의존성을 건다 — `API_AUTH_TOKEN`이 비어 있으면 인증 없이 통과(경고 로그 1회)하고, 값이 있으면 `X-API-Token` 헤더 또는 `api_token` 쿼리 파라미터(SSE의 `EventSource`가 커스텀 헤더를 못 보내기 때문)로 검사한다. 이건 "여러 사용자 인증"이 아니라 로컬에서 도는 다른 프로세스/탭이 실수로 API를 건드리는 걸 막는 최소 방어선이다.

| 메서드/경로 | 설명 |
|---|---|
| `GET /health` | 생존 확인 |
| `GET /prereqs` | `java`/`mvn`/`git`/`python`/`trivy` PATH 점검 결과 |
| `POST /jobs` | Git URL 또는 ZIP(둘 중 정확히 하나) + 옵션(1/2단계 실행 여부)으로 job 생성, 202 즉시 반환. 출력 버전은 인자로 안 받는다 — Stage 0가 자동 계산(§4.1) |
| `GET /jobs` | 전체 job 목록, `created_at` 내림차순 |
| `GET /jobs/{id}` | job 상태 폴링 |
| `POST /jobs/{id}/confirm-version` | `awaiting_version_approval` 상태인 job에 출력 버전을 확인하고 1/2단계 진행(§4.1). 그 상태가 아니면 409, 확인값이 현재 버전과 같아도 409. 사내 parent POM이 감지된 경우 `parent_target_version`도 선택적으로 받는다(§4.2) — 감지된 현재 parent 버전과 같아도 409 |
| `POST /jobs/{id}/proceed` | `awaiting_approval` 상태인 job의 2단계를 재개(§7.4). 그 상태가 아니면 409 |
| `POST /jobs/{id}/resume-stage1` | `stage1_needs_handoff` 상태인 job을, 사람이 `work/`를 직접 고친 뒤 검증하고 1단계를 이어서 진행(§7.6). 그 상태가 아니면 409 |
| `POST /jobs/{id}/cancel` | 터미널이 아닌 job을 강제 중지. 살아있는 Task가 있으면 취소, `awaiting_*` 상태처럼 Task가 없으면 즉시 `cancelled`로 마감 |
| `DELETE /jobs/{id}` | 터미널 상태인 job의 DB 행 + `{JOBS_DATA_DIR}/{job_id}/` 디렉터리를 삭제. 터미널이 아니면 409(먼저 취소 필요) |
| `GET /jobs/{id}/events` | SSE 진행 스트림(재생 + 실시간) |
| `GET /jobs/{id}/artifacts` | diff/report 존재 여부 + handoff 가이드 파일명 목록 |
| `GET /jobs/{id}/artifacts/diff` | `patch.diff` 원문 |
| `GET /jobs/{id}/artifacts/report` | `report.md` 원문 |
| `GET /jobs/{id}/artifacts/handoff/{filename}` | handoff 가이드 원문 (파일명 화이트리스트 검사로 경로 traversal 방지) |
| `GET /jobs/{id}/artifacts/tree` | `work/`의 전체 파일 트리, 파일별 added/modified/unchanged 상태 포함 (`files.html`의 jsTree용) |
| `GET /jobs/{id}/artifacts/file?path=...` | 단일 파일의 baseline 대비 전/후 내용 (`files.html`의 좌우 diff 뷰용, 바이너리 여부 포함) |
| `GET /cache/status` | NVD/Trivy 마지막 갱신 시각(파일 기준) + 현재 갱신 중 여부(§8.4) |
| `POST /cache/refresh` | NVD/Trivy 캐시 갱신을 `cache_refresh` job으로 예약, 202 반환. 이미 갱신 중이면 409 |
| `GET /settings/llm-model` | 사용 가능한 LLM 모델 목록 + 현재 선택된 모델(`.env`의 `LLM_MODEL`) |
| `POST /settings/llm-model` | `.env`의 `LLM_MODEL` 줄만 갱신(다른 줄은 그대로) — 프론트 설정 모달의 모델 드롭다운이 즉시 반영되는 데 씀, 별도 저장 버튼 없음 |

## 12. 프론트엔드 (`frontend/`)

빌드 단계 없는 정적 HTML + vanilla JS, 4개 페이지로 구성 (`index.html`/`history.html`/`job.html`/`files.html`). 공용 로직은 `assets/common.js`(연결 설정 + 설정 모달, 네 페이지 전부)와 `assets/job-view.js`(진행 상황/분석/결과물 뷰, SSE — `job.html` 전용)로 분리한다. `index.html`은 제출 폼 + 정적 설명 콘텐츠만 담당하고 진행 상황은 전혀 다루지 않는다(아래 `index.html` 항목 참고) — `job-view.js`를 아예 로드하지 않는다.

- **연결 설정 + 캐시 상태 + LLM 모델**: 헤더 우상단 설정(⚙) 아이콘 클릭 시 뜨는 모달(네 페이지 모두 동일)에서 API 서버 주소/토큰 입력(`localStorage`, 페이지 간 공유), NVD/Trivy 마지막 갱신 시각 + "지금 갱신" 버튼(§8.4), 그리고 `GET /settings/llm-model`로 채워지는 LLM 모델 드롭다운(§11) — 선택 즉시 반영되고 별도 저장 버튼은 없다. 캐시 갱신은 모달을 열 때마다 `GET /cache/status`를 호출하고, 이미 갱신 중이면 그 job의 SSE에 바로 연결해 아이콘 회전을 이어간다. 갱신 버튼 클릭 시 `POST /cache/refresh` → `job_id`로 `EventSource`를 열어 `log` 이벤트마다 상태 텍스트를 그 메시지로 갱신(별도 로그 패널 없이 "현재 단계" 한 줄만 표시), 종료 상태에서 아이콘 정지 + `GET /cache/status` 재조회.
- **`index.html`**: Git URL 또는 ZIP 업로드(라디오로 전환), 1/2단계 실행 체크박스. 출력 아티팩트 버전 입력 필드는 없다 — Stage 0가 자동 계산해 사람이 확인하는 흐름(§4.1)으로 바뀌었기 때문. 제출 시 `POST /jobs` 202 응답의 `job_id`로 곧장 `job.html?job={job_id}`로 이동한다 — `index.html` 자체는 진행 상황을 보여주지 않는다(SSE 연결도 안 함). 제출 폼 아래에는 "파이프라인 동작 방식" 구분선과 함께 Stage 0/1/2를 요약하는 정적 LangGraph 다이어그램 3개(`stage0-overview`/`stage1-overview`/`stage2-overview`)가 있다 — Stage 1/2 다이어그램은 아래 진행 상황 뷰의 도움말 모달과 같은 SVG를 그대로 재사용하고, Stage 0(선형 흐름이라 자가검증 루프가 아님)는 별도로 그렸다. 실시간 데이터가 아니라 순수 설명용 콘텐츠라는 점을 문구로 명시해둔다.
- **진행 상황 뷰**(`job-view.js`, `job.html` 전용): `log`/`status`/`inventory`/`vulnerabilities_baseline`/`vulnerabilities_post_stage1`/`vulnerabilities`/`vulnerabilities_final` 이벤트를 실시간 렌더링 — "분석" 카드(감지된 스택 + 취약점 테이블 최대 4개: 마이그레이션 전/후, 2단계 패치 대상, 최종 — §4.1, §7.5, §8.1, §8.3)가 진행 상황 카드 **아래**에 표시된다. "진행 상황" 제목 옆 "?" 아이콘으로 LangGraph 오케스트레이션(§7.2, §8.2) 다이어그램 모달을 볼 수 있다.
  - `status`가 `awaiting_version_approval`이면 감지된 현재/제안 버전을 보여주는 확인 패널이 뜬다(입력창에 제안값 프리필) — "확인하고 계속" 클릭 시 `POST /jobs/{id}/confirm-version` 호출, 동일 버전이면 백엔드가 409를 돌려주고 그 에러를 로그에 남긴 뒤 재시도 가능하게 둔다(§4.1).
  - `status`가 `awaiting_approval`이면 "2단계로 진행(승인)" 버튼이 나타나고(§7.4), 클릭 시 `POST /jobs/{id}/proceed` 호출 후 버튼만 숨김 — SSE는 재연결 없이 이어지는 이벤트를 그대로 받는다.
  - `status`가 `stage1_needs_handoff`이면 "인수인계 후 재개" 버튼이 나타나고(§7.6), 클릭 시 `POST /jobs/{id}/resume-stage1` 호출 후 버튼을 숨긴다 — 이 상태는 터미널이라 SSE가 이미 닫혀 있으므로(§10), `proceed`와 달리 `connectSSE`를 다시 호출해 재연결한다.
  - 터미널이 아닌 상태에서는 "중지" 버튼이 떠 있고, 클릭(확인 다이얼로그 후) 시 `POST /jobs/{id}/cancel`을 호출한다.
  - 종료 상태 도달 시 연결을 닫고 `GET /jobs/{id}/artifacts` 조회.
- **결과물 뷰어**: diff/report/handoff 각각 클릭 시 원문을 불러와 표시, 복사(클립보드)와 다운로드(Blob) 버튼 제공. diff가 있으면 "파일별로 보기" 링크로 `files.html?job={id}`로 이동할 수 있다.
- **`history.html`**: `GET /jobs`로 전체 이력을 최신순 테이블로 표시, job_id 클릭 시 `job.html?job={id}`로 이동. 진행 중인 job 행에는 "중지"(`POST /jobs/{id}/cancel`) 버튼이, 터미널 상태인 job 행에는 "삭제"(`DELETE /jobs/{id}`, 확인 다이얼로그 후) 버튼이 뜬다 — 중지되면 그 행이 자동으로 "삭제" 버튼으로 바뀐다. `source_type="cache_refresh"` job은 목록에 나타나지 않는다(§8.4).
- **`job.html`**: URL 쿼리의 `job_id`로 `GET /jobs/{id}` 조회 후 진행 상황 뷰(`job-view.js`)를 로드 — `index.html`에서 막 제출한 job이든 `history.html`에서 클릭해 들어온 이전 job이든 같은 화면을 쓴다. 종료된 job은 SSE가 히스토리를 replay하고 바로 닫히므로 "로그 다시 보기"로도 동작.
- **`files.html`**: `GET /jobs/{id}/artifacts/tree`로 받은 파일 트리를 jsTree(`assets/vendor/`에 로컬 번들, CDN 아님)로 렌더링 — 기본 접힘, `target/`/`.git` 등 노이즈 디렉터리 제외, 폴더가 파일보다 먼저 정렬, "전체 펼치기"/"전체 접기" 버튼. 파일 클릭 시 `GET /jobs/{id}/artifacts/file?path=...`로 baseline 대비 전/후 내용을 좌우로 보여준다(바이너리면 미리보기 대신 안내 문구, 새로 추가된 파일은 왼쪽에 안내 문구).
- CORS: 백엔드의 `CORS_ALLOW_ORIGINS`가 프론트엔드가 뜬 오리진과 일치해야 함(기본 `http://localhost:5500`). `file://`로 직접 열지 말 것(오리진이 `null`이라 CORS 처리가 예측 불가).

## 13. 설정 (`config.py`, `.env`)

`Settings`는 `pydantic-settings`로 `backend/.env`를 읽으며, 모든 상대 경로는 **프로세스 CWD가 아니라 `backend/` 기준**으로 resolve된다(`resolve_path`) — 그렇지 않으면 최상위 `data/`(참고 zip들)와 충돌할 수 있기 때문.

| 분류 | 주요 값 |
|---|---|
| LLM | `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_MONTHLY_BUDGET_USD` |
| SCA | `NVD_API_KEY`, `DEPENDENCY_CHECK_DATA_DIR`, `TRIVY_CACHE_DIR`, `FAIL_ON_CVSS` |
| Git | `GIT_AUTHOR_NAME`/`EMAIL`, `GIT_TOKEN`, `GIT_SSH_KEY_PATH` |
| 앱 보안 | `API_AUTH_TOKEN` |
| DB/작업 데이터 | `DATABASE_URL`(SQLite), `JOBS_DATA_DIR` |
| 네트워크 | `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` (모든 서브프로세스 호출에 `procenv.build_subprocess_env`로 명시적으로 주입 — `.env` 값이 `os.environ`에 자동 반영되지 않기 때문) |
| 한도/안전장치 | `MAX_CONCURRENT_REPOS`, `BUILD_TIMEOUT_SECONDS`, `COMPILE_FIX_MAX_ATTEMPTS`, `COMPILE_FIX_AUTO_APPLY_MAX_FILES`, `UPLOAD_MAX_MB`/`_EXTRACTED_MB`/`_FILES` |
| LangSmith | `LANGSMITH_API_KEY`/`_TRACING`/`_PROJECT`/`_ENDPOINT` — `configure_langsmith_env()`가 앱 기동 시 `os.environ`으로 명시적으로 내보내야 LangChain 자동 트레이싱이 실제로 켜진다 |
| 서버 | `HOST`, `PORT`, `LOG_LEVEL`, `CORS_ALLOW_ORIGINS` |
| 예약(미구현) | `EMBEDDING_API_KEY`/`_MODEL`, `NEXUS_URL`/`_USERNAME`/`_PASSWORD`, `SLACK_WEBHOOK_URL`, `SMTP_*`, `CI_TRIGGER_TOKEN`, `INVENTORY_DEEP_AGENT_ENABLED`/`PLAN_DEEP_AGENT_ENABLED`/`_CONFIDENCE_THRESHOLD` |

## 14. 배포/실행 모델

중앙 호스팅 서비스가 아니라, **각 시스템 담당 개발자가 자기 PC에서 백엔드(uvicorn)와 프론트엔드(정적 서버)를 각각 띄워 로컬로 실행**하는 모델이다. `MAX_CONCURRENT_REPOS`는 여러 사용자를 나누는 값이 아니라 로컬 머신 하나가 동시에 버틸 수 있는 `mvn` 빌드 수의 안전장치다.

Windows에서는 `uvicorn --reload`를 쓰면 안 된다 — reload가 이벤트 루프를 `SelectorEventLoop`로 강제하는데, 이는 `asyncio` 서브프로세스(`create_subprocess_exec`)를 지원하지 않아 첫 비동기 서브프로세스 호출에서 job이 원인 메시지 없이 `failed`로 끝난다(`backend/README.md` §4).

## 15. 스펙 대비 구현 현황

| 스펙 항목 | 상태 |
|---|---|
| Git/ZIP 인입, 경로 traversal/zip bomb 방지 | 구현됨 (`ingest/`) |
| source/work/output 분리, git 체크포인트/롤백 | 구현됨 (`checkpoint/git_repo.py`) |
| 단계적 마이그레이션 계획 (Java→Boot 홉별→Cloud/AI 결합) | 구현됨 (`orchestration/planning.py`) |
| LangGraph 기반 자가검증 루프 (Stage 1/2) | 구현됨 (`graph_stage1.py`, `graph_stage2.py`) |
| 자동 적용 범위 제한, 재시도 상한 | 구현됨 |
| AI 인수인계 가이드 | 구현됨, 단 LLM 재호출 없이 기존 대화/빌드 로그로 템플릿 조립 |
| OWASP Dependency-Check + Trivy 병렬 스캔/병합/CVSS 필터 | 구현됨 (`scan/`) |
| 출력 아티팩트 버전 설정 (`versions:set`) | 구현됨 (`versioning/`), 단 원래 스펙의 "선택 입력"에서 "Stage 0가 자동 계산 + 사람이 확인"으로 정책 변경(§4.1) — 동일 버전으로는 진행 불가(사내 Nexus 재배포 시 덮어쓰기 방지) |
| SSE 진행 스트리밍 (재생 + 실시간) + 로컬 LLM 호출 로그 | 구현됨 (`streaming/`, `orchestration/callbacks.py`) |
| LangSmith 트레이싱 | 구현됨 (환경변수 브릿지 포함) |
| 토큰 기반 최소 인증 | 구현됨 (`api/deps.py`) |
| 작업 강제 중지/삭제 | 구현됨 (`POST /jobs/{id}/cancel`, `DELETE /jobs/{id}`) — 원래 스펙에는 없던 항목 |
| 파일별 diff 뷰어 (`files.html`) | 구현됨 — 원래 스펙에는 없던 항목, jsTree 기반 |
| Stage 2 verify에서 CVE별 Dependency-Check/Trivy 재실행 | **부분 구현** — 배치 전체 단위로는 실행 전(Stage 0 베이스라인 또는 §7.5)/후(§8.3 최종 스캔) 재스캔이 있다. CVE 하나하나 단위의 재검증은 여전히 없음(빌드 검증만) |
| RAG/임베딩 (`EMBEDDING_API_KEY` 등) | **미구현** — 설정값만 예약 |
| deepagents 기반 `create_deep_agent` (계획 수립용 deep agent) | **미구현** — 의존성은 `pyproject.toml`에 있고 `INVENTORY_DEEP_AGENT_ENABLED`/`PLAN_DEEP_AGENT_ENABLED` 설정값도 있으나 실제 호출 코드는 없음(`get_chat_model` + `create_agent` 기반의 단순 에이전트만 사용) |
| Nexus 배포 연동 | **미구현** — 설정값만 예약 |
| Slack/이메일 알림 | **미구현** — 설정값만 예약 |
| Gradle 지원 | 범위 외 (감지 시 명시적 에러) |

## 16. 참고

- LangGraph 그래프별 노드 상세(Stage 1/2 자가검증 루프): [`docs/langgraph-orchestration.md`](langgraph-orchestration.md)
- 설계 배경/의사결정 근거: [`docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md`](superpowers/specs/2026-08-06-oss-dependency-governance-design.md)
- Stage 0 도입 배경(§4.1) — 출력 버전 자동화 + 스캔 재배치: [`docs/superpowers/specs/2026-08-10-stage0-version-scan-restructure-design.md`](superpowers/specs/2026-08-10-stage0-version-scan-restructure-design.md), 구현 순서: [`docs/superpowers/plans/2026-08-10-stage0-version-scan-restructure-plan.md`](superpowers/plans/2026-08-10-stage0-version-scan-restructure-plan.md)
- 백엔드 실행 방법: [`backend/README.md`](../backend/README.md)
- 프론트엔드 실행 방법: [`frontend/README.md`](../frontend/README.md)
- 레시피 카탈로그(현재 커버리지, confidence 표기): [`backend/app/mvnrewrite/recipe_catalog.yaml`](../backend/app/mvnrewrite/recipe_catalog.yaml)
