# 구현 계획 — 사내 parent POM(BOM 겸용) 목표 버전 전이

스펙: [`docs/superpowers/specs/2026-08-11-internal-parent-pom-target-version-design.md`](../specs/2026-08-11-internal-parent-pom-target-version-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아 기존 계획 문서 형식(`2026-08-10-stage0-version-scan-restructure-plan.md`)을 따라 직접 작성했다. 스펙에 실제 코드가 거의 그대로 들어 있으므로, 여기서는 반복하지 않고 파일별 적용 순서와 검증만 정리한다. 순서: 감지(순수 함수) → parent 교체(mechanical, 독립 모듈) → Stage 1 그래프 확장 → multi_step 재구성 → pipeline.py/jobs.py 배선 → 프론트엔드 → 전체 검증.

## 0. 사전 확인

- `git status` 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 베이스라인 확인.
- **주의**: `run_stage1_migration`에 새 선택 인자(`parent_target_version`)가 늘어나지만 기존 호출부는 그대로 컴파일된다(기본값 `None`) — 시그니처 파괴적 변경은 없다. `run_pipeline_resume_after_version_confirm`도 마찬가지.

## 1. `ingest/maven_detect.py` — 사내 parent 감지

스펙 코드 그대로 `_PUBLIC_PARENT_ALLOWLIST`, `ExternalParentInfo`, `detect_external_parent` 추가(기존 `read_declared_version` 아래, 기존 `_parse_pom`/`_text` 헬퍼 재사용).

**검증**: `backend/tests/unit/test_maven_detect.py`에 추가:
- 공개 parent(`org.springframework.boot:spring-boot-starter-parent`) → `None`.
- `ace-parent` 형태(사내, 허용목록 밖) → `ExternalParentInfo(group_id="com.poscodx.ai.ace", artifact_id="ace-parent", version="0.4.5")`.
- `<parent>` 자체가 없는 pom → `None`.

## 2. `mvnrewrite/parent_patch.py` — 신규, parent 버전 교체 (mechanical)

스펙 코드 그대로 `patch_parent_version(pom_path, new_version)` 추가.

**검증**: `backend/tests/unit/test_parent_patch.py`(신규 파일) 추가:
- `<parent><version>`만 정확히 바뀌고 `groupId`/`artifactId`는 그대로인지.
- `<parent>` 없는 pom → `ValueError`.
- `<parent>`에 `<version>` 없는 pom → `ValueError`.

**통합** (실제 `mvn`, `slow` 마커): `backend/tests/integration/test_parent_patch.py`(신규) — `ace-parent.zip`을 로컬 `.m2`에 두 버전(`0.4.5`, 가짜 `0.5.0`)으로 `install -N`해두고, `anne-agent.zip`을 인입해 `patch_parent_version` 적용 후 `mvn help:evaluate -Dexpression=project.parent.version`으로 정확히 그 값이 resolve되는지 확인 — 이번 조사에서 실측한 `versions:update-parent`의 "지정한 버전보다 큰 값으로 새는" 실패 사례를 회귀 테스트로 고정하는 게 목적이므로, 가짜 `0.5.0` 외에 로컬에 그보다 "더 큰" 버전(예: 과거 테스트가 남긴 `1.0.0`, `4.1` 등)이 이미 설치돼 있어도 정확히 `0.5.0`으로 고정되는지까지 확인한다(실제로 이번 조사 중 로컬 `.m2`가 여러 버전으로 오염된 상태에서 문제를 발견했음 — 그 오염된 상태 자체가 유효한 회귀 조건).

## 3. Stage 1 그래프 확장 (`planning.py`, `state.py`, `graph_stage1.py`)

- `orchestration/planning.py`: `StepKind`에 `"parent_pom"` 추가. `build_migration_plan` 자체는 변경 없음(parent 스텝은 §5의 `multi_step.py`가 직접 조립).
- `orchestration/state.py`: `Stage1State`에 `step_kind: StepKind` 필드 추가.
- `orchestration/graph_stage1.py`: 스펙 코드대로 5곳 수정 —
  - `initial_state_for_step`: `step_kind=step.kind` 추가.
  - `route_after_plan`: `step_kind == "parent_pom"`이면 `recipe`가 `None`이어도 `apply`로(기존엔 `ai_fix`로 샜음).
  - `apply_node`: `step_kind == "parent_pom"` 분기 추가(`patch_parent_version` 호출 + 체크포인트 커밋, 기존 OpenRewrite 분기는 그대로 둠).
  - `ai_fix_node`: `step_kind == "parent_pom"` 전용 프롬프트 분기 추가(기존 "레시피 없음, attempt==0" 분기와 섞이지 않도록 그보다 먼저 검사).
  - `verify_node`/`route_after_verify`/`route_after_ai_fix`/`route_after_apply`/`handoff_node`는 이미 범용적이라 변경 없음.

**검증**: `backend/tests/unit/test_graph_stage1.py`(기존 파일 있으면 거기에 추가, 없으면 신규)에 `step_kind="parent_pom"`으로 그래프를 직접 `ainvoke`하는 테스트 3개:
- `patch_parent_version`이 성공하고 `mvn test-compile`도 성공 → `status="success"`, 체크포인트 커밋 1개(parent 교체) 확인.
- `patch_parent_version`은 성공했지만 `mvn test-compile` 실패 → `ai_fix`로 가서 "parent 교체 관련" 프롬프트가 쓰였는지(mock으로 `create_agent`/에이전트 호출 가로채서 전달된 instruction 문자열 확인), 재시도 소진 후 `handoff`.
- `patch_parent_version` 자체가 예외(`<parent>` 없음) → `apply_returncode=1`로 기록되고 바로 `verify`(실패) → `ai_fix` → 소진 → `handoff` 경로를 타는지(별도 즉시-실패 분기를 안 만들었으므로 기존 재시도 경로를 그대로 타는 게 맞음 — 스펙의 "존재하지 않는 버전"과 동일한 처리).

## 4. `orchestration/multi_step.py` — parent 스텝 실행 + 재계획

스펙 코드 그대로 `run_stage1_migration` 재구성 — `parent_target_version` 파라미터 추가, `output_dir`은 새 파라미터로 안 받고 `work_dir.parent / "output"`로 내부 유도, parent 스텝 성공 시 `mvn_effective_pom`/`extract_versions` 재호출로 나머지 계획 재수립.

- import 추가: `from app.mvnrewrite.mvn_client import mvn_effective_pom`, `from app.mvnrewrite.pom_parser import extract_versions`, `from app.mvnrewrite.subprocess_runner import build_log_path`.

**검증**: `backend/tests/unit/test_multi_step.py`에 추가(기존 테스트들은 `parent_target_version` 기본값 `None`이라 안 건드려도 그대로 통과해야 함 — 이것부터 먼저 돌려서 회귀 없는지 확인):
- `parent_target_version` 없이 호출 → 기존 테스트 전부 그대로 통과(회귀 없음 확인용, 새 assert 불필요).
- `parent_target_version` 있음 + parent 스텝 성공(mock) → `plan.steps[0].kind == "parent_pom"`, `mvn_effective_pom`/`extract_versions`가 정확히 1번씩 재호출되는지(mock call count), 그 재호출 결과로 나머지 계획이 세워지는지(재호출 결과를 이미 목표 버전으로 mock하면 나머지 계획이 빈 채로 `status="success"`인지 — `no_gap`이 아니라).
- `parent_target_version` 있음 + parent 스텝 실패(mock) → `mvn_effective_pom`/`extract_versions`가 전혀 호출 안 되는지(재분석 자체를 안 함), 나머지 계획도 안 세워지고(`plan.steps`가 parent 스텝 하나뿐) 바로 `status="needs_handoff"`로 끝나는지.

## 5. `orchestration/pipeline.py`, `schemas/job.py`, `api/routers/jobs.py`

- `pipeline.py`의 Stage 0 블록(`run_pipeline`): `mvn_effective_pom`/`extract_versions` 직후 `detect_external_parent(work_dir / "pom.xml")` 호출, `status` 이벤트에 `detected_parent` 필드 추가.
- `run_pipeline_resume_after_version_confirm`: `parent_target_version: str | None = None` 파라미터 추가, `run_stage1_migration` 호출에 그대로 전달. `run_stage1`이 아닌 경우(2단계만) `parent_target_version`은 그냥 무시됨(스펙에 별도 처리 없음 — parent 개념 자체가 Stage 1 전용).
- `detected_parent is not None and not confirmed 시점의 parent_target_version`인 경우 리포트에 안내 문구 추가하는 로직 위치 확정 필요(§ "참고" 참고).
- `schemas/job.py`: `ConfirmVersionRequest`에 `parent_target_version: str | None = None` 추가.
- `api/routers/jobs.py`의 `confirm_version`: import에 `detect_external_parent` 추가. 기존 "출력 버전 동일값 409" 검사 아래에 "parent 목표 버전 동일값 409" 검사 추가(스펙 코드 그대로), `run_pipeline_resume_after_version_confirm` 호출에 `parent_target_version=body.parent_target_version` 전달.

**검증**: `backend/tests/integration/test_jobs_api.py`에 추가(기존 `app_client`/`monkeypatch` 패턴 재사용, `ace-parent`류 `<parent>`가 있는 fixture pom을 zip으로 만들어 제출):
- `test_job_reaches_awaiting_version_approval_with_detected_parent`: 사내 parent가 있는 프로젝트 제출 → SSE `status` 이벤트에 `detected_parent`가 실리는지(공개 parent/무parent 프로젝트에서는 `None`인지도 같이 확인).
- `test_confirm_version_with_same_parent_target_version_returns_409`.
- `test_confirm_version_with_parent_target_version_reaches_stage1`: `parent_target_version` 포함해 확인 → `run_stage1_migration`이 그 값을 받아 호출되는지(monkeypatch로 `run_stage1_migration`을 가로채서 전달된 인자 확인 — 실제 mvn까지는 안 돌림, 그건 §2의 통합 테스트가 이미 커버).

## 6. 프론트엔드

### `index.html`, `job.html` (동일하게 두 파일 모두)

`version-approval-panel` 안, 기존 "적용할 출력 버전" `field-row` 아래에 스펙의 `parent-version-field`(기본 `hidden`)와 `parent-version-hint` 추가.

### `assets/job-view.js`

- 엘리먼트 참조 추가: `parentVersionField`, `parentTargetVersionInput`, `parentVersionHint`.
- `status` 이벤트 핸들러의 `awaiting_version_approval` 분기: `data.detected_parent`가 있으면 `parentVersionField` 표시 + 안내 문구 채움, 없으면 숨김(+ 입력값 비움).
- `confirmVersionBtn` 클릭 핸들러의 요청 바디에 `parent_target_version: parentTargetVersionInput.value.trim() || null` 추가.

**검증**: `node --check frontend/assets/job-view.js`.

## 7. `frontend/README.md` — 체크리스트 추가

기존 Stage 0 관련 체크리스트 절 근처에 항목 추가:
- 사내 parent가 있는 프로젝트 제출 → 버전 확인 패널에 "사내 parent POM 목표 버전" 입력창이 추가로 뜨는지(그 외 프로젝트에서는 안 뜨는지).
- 값을 비운 채 확인 → 기존 동작(이 프로젝트 파일만 대상)과 동일하게 진행되는지, 스택 프로퍼티가 전부 parent 상속이면 리포트에 안내 문구가 남는지.
- 값을 입력하고 확인 → 진행 로그에 parent 교체 스텝이 먼저 실행되고, 그 뒤 "재분석" 로그가 남는지, 이후 계획이 재분석된 스택 기준으로 세워지는지.

## 8. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합(기본, `slow`/`external` 제외) 전체 통과 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q -m slow --basetemp=/c/pytesttmp`로 `parent_patch`/`artifact_version` 등 실제 `mvn` 통합 테스트 통과 확인.
- 실제 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고, `ace-parent.zip`/`anne-agent.zip`(또는 실제 사내 저장소)으로:
  1. `anne-agent`를 1단계 선택으로 제출 → Stage 0 완료 후 버전 확인 패널에 "사내 parent POM 목표 버전" 입력창이 추가로 뜨는지(현재 parent 버전 표시 포함).
  2. 값을 비운 채 확인 → 1단계가 기존처럼 진행되고(스택 프로퍼티가 상속이라 대부분 변경 없음), 리포트에 안내 문구가 남는지.
  3. (사전에 로컬 `.m2` 또는 테스트용 Nexus에 목표 스택을 반영한 가짜 새 parent 버전을 준비해두고) 그 버전을 입력해 확인 → 진행 로그에 parent 교체 스텝 → 재분석 로그 → 이어지는 계획이 뜨고, 최종적으로 목표 스택(또는 남은 갭)에 맞게 끝나는지.
  4. 동일한 parent 버전을 두 번째로 입력 시도 → 409.
  5. 공개 parent(`spring-boot-starter-parent`)를 쓰는 `sample-legacy-app.zip`(확인 완료 — `data/` 참고 저장소 4개 중 이것만 공개 parent, 나머지 `ace-portal`/`daisy-agent`는 `anne-agent`와 마찬가지로 `ace-parent` 상속) 제출 → parent 확인 입력창 자체가 안 뜨는지(회귀 확인).

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

- **리포트 안내 문구의 정확한 삽입 위치**: 스펙은 "`detected_parent is not None and not parent_target_version`이면 리포트 끝에 안내 문구를 덧붙인다"고만 정했다. `build_report`(순수 함수, `plan`/`outcomes`만 봄) 안에 넣을지, 아니면 `pipeline.py`의 `run_pipeline_resume_after_version_confirm`이 `build_report`가 만든 문자열 뒤에 그냥 이어붙일지는 구현 시 정한다 — 후자가 `build_report`의 책임 범위(리포트 "내용"만, "이 job에 뭐가 감지됐는지"는 모름)를 안 건드려서 더 간단해 보인다.
- **`detected_parent`를 `run_pipeline_resume_after_version_confirm`에 어떻게 넘길지**: Stage 0(`run_pipeline`)에서 감지한 값을 (a) DB의 `JobEvent`(`status` 이벤트의 `detected_parent` 필드)에서 다시 읽어오거나, (b) `confirm_version` 엔드포인트가 이미 한 번 더 감지(§5의 409 검사용)한 값을 `run_pipeline_resume_after_version_confirm` 인자로 그대로 넘기거나 — 기존 `output_version` 확인 로직이 (a) 방식(엔드포인트에서 다시 `read_declared_version` 호출, DB 이벤트 재사용 안 함)을 쓰고 있으므로 일관성을 위해 여기서도 (a)와 같은 모양으로, `run_pipeline_resume_after_version_confirm`이 필요 시 `work_dir/pom.xml`에 대해 `detect_external_parent`를 다시 한번 호출하는 쪽을 기본으로 하되, 구현 시 재확인.
- **`parent-version-field`의 정확한 DOM 삽입 위치**는 스펙에 정해져 있지 않음(§6) — 기존 `field-row` 흐름을 깨지 않는 선에서 구현 시 정한다.
