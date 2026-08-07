# SQLite 데이터베이스 (`backend/data/app.db`)

- 작성일: 2026-08-07
- 대상: `backend/app/models/db.py`, `backend/app/models/job.py`에 정의된 실제 스키마. `backend/data/app.db`(기본 `DATABASE_URL=sqlite:///./data/app.db`, [`backend/.env.example`](../backend/.env.example))의 실제 테이블 정의(`sqlite_master`)와 대조해 일치를 확인했다.
- 아키텍처 전반 맥락은 [`docs/architecture.md`](architecture.md) §10 "Job 상태/진행 스트리밍"을 함께 참고.

## 개요

- 엔진: SQLite. `Settings.database_url_resolved`가 상대 경로를 `backend/` 기준으로 resolve한다 (`app/config.py`) — 그래서 기본값이 `backend/data/app.db`가 된다.
- 마이그레이션 프레임워크 없음: `models/db.py`의 `init_db()`가 앱 기동 시 `Base.metadata.create_all(engine)`로 "없으면 생성"만 한다(ddl-auto 방식). 스키마 변경 시 별도 마이그레이션 스크립트가 없으므로, 컬럼을 추가/변경하면 기존 `app.db` 파일과 어긋날 수 있다.
- 테이블은 **`jobs`, `job_events` 단 2개**뿐이다. 이 도구가 다루는 실제 산출물(diff, report, effective-pom.xml, 서브프로세스/LLM 로그 등)은 DB가 아니라 파일시스템(`JOBS_DATA_DIR/<job_id>/output/...`)에 저장된다 — DB는 오직 **job의 메타데이터와 진행 이벤트 타임라인**만 담당한다.
- `job_events.job_id`는 `jobs.id`를 논리적으로 참조하지만, SQLAlchemy 모델에 `ForeignKey`가 선언되어 있지 않다 — **DB 레벨 참조 무결성 제약은 없다** (아래 "관계" 참고).

## ERD

```mermaid
erDiagram
    JOBS ||--o{ JOB_EVENTS : "job_id (논리적 참조, FK 제약 없음)"

    JOBS {
        string id PK "순번 문자열, 예: '1' (models/job.py: next_job_id)"
        string source_type "git | zip"
        string source_ref "git URL 또는 원본 업로드 파일명"
        string output_version "선택값, NULL이면 원본 버전 유지"
        boolean run_stage1
        boolean run_stage2
        string status "queued|running|success|needs_handoff|failed"
        string error_message "NULL 가능"
        string report_markdown "NULL 가능, 최종 리포트 전문"
        datetime created_at
        datetime updated_at
    }

    JOB_EVENTS {
        integer id PK "autoincrement"
        string job_id FK "jobs.id를 가리킴 (제약 없음), 인덱스 있음"
        integer seq "job별 발행 순서, 1부터"
        string event_type "log | status"
        json data "이벤트 타입별 payload"
        datetime created_at "server_default=CURRENT_TIMESTAMP"
    }
```

## 테이블: `jobs`

한 번의 인입→(선택)버전설정→1단계→2단계→산출물 생성 실행 전체를 나타내는 행. `POST /jobs`가 한 행을 만들고, 백그라운드로 도는 `orchestration/pipeline.run_pipeline`이 진행하며 같은 행을 갱신한다.

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| `id` | VARCHAR | NOT NULL (PK) | — | `models/job.py`의 `next_job_id(db)`가 발급하는 **로컬 순번 문자열**(`"1"`, `"2"`, ...). UUID가 아니라 이 DB에 쌓인 job 개수를 세어 +1한 값 — 사람이 읽기 쉽고, `JOBS_DATA_DIR/<id>/{source,work,output}/` 디렉토리명과 그대로 일치한다. job은 삭제 엔드포인트가 없어 append-only이므로 카운트 기반 채번이 안전하다. |
| `source_type` | VARCHAR | NOT NULL | — | `"git"` 또는 `"zip"`. |
| `source_ref` | VARCHAR | NOT NULL | — | `source_type="git"`이면 Git URL, `"zip"`이면 업로드 원본 파일명. |
| `output_version` | VARCHAR | NULL 허용 | — | 사용자가 지정한 출력 아티팩트 버전(`versions:set` 대상 값). 비워두면 NULL — 원본 버전 유지. |
| `run_stage1` | BOOLEAN | NOT NULL | `True` | 1단계(스택 마이그레이션) 실행 여부. |
| `run_stage2` | BOOLEAN | NOT NULL | `False` | 2단계(개별 CVE 패치, 옵션) 실행 여부. |
| `status` | VARCHAR | NOT NULL | `"queued"` | `JOB_STATUSES = ("queued", "running", "success", "needs_handoff", "failed")` 중 하나. DB 레벨 CHECK 제약이나 enum 타입은 쓰지 않는다 — SQLite에 네이티브 enum이 없고, 마이그레이션 없이 상태값을 늘릴 수 있게 하려는 의도적 선택(코드 주석 참고). `TERMINAL_JOB_STATUSES = {"success", "needs_handoff", "failed"}`가 종료 상태 집합. |
| `error_message` | VARCHAR | NULL 허용 | — | `status="failed"`일 때 예외 메시지. |
| `report_markdown` | VARCHAR | NULL 허용 | — | 최종 리포트(`output/report.md`와 동일 내용)의 전문. 종료 상태에서만 채워짐. |
| `created_at` | DATETIME | NOT NULL | `datetime.now(UTC)` (Python 쪽 default) | 행 생성 시각. |
| `updated_at` | DATETIME | NOT NULL | `datetime.now(UTC)`, `onupdate`로 갱신 | 마지막 갱신 시각. |

**인덱스**: `sqlite_autoindex_jobs_1` (PK `id`에 대한 SQLite 자동 인덱스). 그 외 별도 인덱스 없음.

## 테이블: `job_events`

진행 상황 타임라인의 한 항목. `GET /jobs/{id}/events`(SSE)가 과거 이벤트를 이 테이블에서 재생한 뒤 실시간 이벤트로 전환한다 (`streaming/sse.py`). 저장은 `streaming/events.emit_event()`가 담당하며, 저장과 동시에(같은 함수 안에서) 인메모리 pub/sub(`streaming/bus.py`)로도 발행해 열려 있는 SSE 연결에 즉시 흘려보낸다.

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| `id` | INTEGER | NOT NULL (PK, autoincrement) | — | 테이블 전역 자동증가 PK. `seq`와 달리 job을 넘나드는 값이라 클라이언트에는 노출되지 않는다. |
| `job_id` | VARCHAR | NOT NULL | — | `jobs.id`를 가리키는 논리적 참조. **인덱스(`ix_job_events_job_id`) 있음**, FK 제약은 없음. |
| `seq` | INTEGER | NOT NULL | — | 같은 `job_id` 안에서 1부터 증가하는 순번 (`streaming/events._next_seq`가 `MAX(seq)+1`로 채번). SSE의 이벤트 재생 순서 보장 및 재연결 시 중복 전달 방지에 쓰인다. |
| `event_type` | VARCHAR | NOT NULL | — | 실제로 쓰이는 값은 **`"log"`, `"status"`** 두 가지뿐 (코드 주석은 `"llm"`도 언급하지만 실제로 발행되는 곳은 없다 — LLM 호출 로그는 이 테이블이 아니라 `output/logs/<stage>/llm/*.json` 파일로 별도 저장됨, [`architecture.md`](architecture.md) §7.2 참고). SSE에는 `job_id`를 찾을 수 없을 때만 나오는 `"error"` 타입도 있지만, 이건 DB에 저장되지 않는 스트림 전용 합성 이벤트다. |
| `data` | JSON | NOT NULL | — | `event_type`별 payload. `"log"` → `{"message": "..."}`. `"status"` → `{"status": "running"\|"success"\|"needs_handoff"\|"failed"}`, 실패 시 `{"status": "failed", "error": "..."}`처럼 `error` 키가 추가됨. |
| `created_at` | DATETIME | NOT NULL | `CURRENT_TIMESTAMP` (SQLite 서버 기본값) | 행 생성 시각. `jobs`와 달리 Python 쪽이 아니라 **DB 서버 기본값**을 쓴다(`server_default=func.now()`). |

**인덱스**: `sqlite_autoindex_job_events_1`(PK `id`), `ix_job_events_job_id`(컬럼 `job_id`) — `WHERE job_id = ?` 조회(재생, 카운트)를 위한 것.

## 관계

```
jobs (1) ──< job_events (N)
```

- 하나의 `jobs` 행은 여러 `job_events` 행을 갖는다(진행되면서 계속 `emit()`됨). 실제 운영 데이터 기준(2026-08-07 시점, job 3건) 평균 job당 약 9개의 이벤트가 쌓여 있다.
- **SQLAlchemy 모델에 `ForeignKey("jobs.id")`가 선언되어 있지 않다** — 즉 SQLite가 참조 무결성을 강제하지 않는다(`job_events.job_id`에 존재하지 않는 `jobs.id` 값이 들어가도 에러가 나지 않는다). 애플리케이션 코드가 항상 `job_id`를 job 생성 직후에만 이벤트 발행에 사용하므로 실무상 깨질 일은 없지만, DB 자체가 이를 보장하지는 않는다는 점은 스키마를 바꿀 때 유의할 부분이다.
- 삭제 API가 없어 두 테이블 모두 **append-only**다. `job_events`를 지우는 코드 경로도 없다.

## 참고

- `backend/app/models/db.py` — 엔진/세션 생성, `init_db()`(create-if-missing), 경로 resolve 로직.
- `backend/app/models/job.py` — `Job`/`JobEvent` 모델, 상태값 상수, `next_job_id()`.
- `backend/app/streaming/events.py` / `backend/app/streaming/sse.py` — `job_events` 쓰기/재생 로직.
- [`docs/architecture.md`](architecture.md) — 전체 아키텍처, 이 DB가 파이프라인/SSE와 어떻게 맞물리는지.
