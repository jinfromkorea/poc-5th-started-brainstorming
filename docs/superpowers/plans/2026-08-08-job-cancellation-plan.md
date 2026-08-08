# 구현 계획 — 작업 강제 중지 (Job Cancellation)

스펙: [`docs/superpowers/specs/2026-08-08-job-cancellation-design.md`](../specs/2026-08-08-job-cancellation-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아 이 문서는 브레인스토밍 결과를 바탕으로 직접 작성했다. 단계는 의존성 순서(모델 → 동시성 유틸 → 서브프로세스 → 파이프라인 → API → 프론트엔드 → 테스트)를 따른다. 각 단계 뒤에 "검증"을 명시했으니, 구현 중 막히면 이전 단계로 돌아가지 말고 해당 단계의 검증부터 다시 확인한다.

## 0. 사전 확인

- 현재 `git status`가 깨끗한지 확인하고 시작한다(이 계획과 무관한 미커밋 변경과 섞이지 않도록).
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` 로 기존 테스트가 전부 통과하는 베이스라인을 확인한다.

## 1. `models/job.py` — 상태 모델

- `JOB_STATUSES`에 `"cancelled"` 추가.
- `TERMINAL_JOB_STATUSES`에 `"cancelled"` 추가.
- 이 두 상수를 쓰는 곳 확인: `streaming/sse.py`(`TERMINAL_JOB_STATUSES`로 SSE 스트림 종료 시점 판단 — 코드 변경 불필요, 상수만 갱신하면 자동으로 `cancelled`에서도 스트림이 닫힘).

**검증**: `grep -rn "JOB_STATUSES\|TERMINAL_JOB_STATUSES" backend/app`로 참조하는 곳을 다시 훑어 빠뜨린 분기가 없는지 확인. 기존 단위 테스트(`test_health.py` 등)가 여전히 통과하는지.

## 2. `orchestration/concurrency.py` — `JobManager`

- `cancel(job_id: str) -> bool` 추가: 등록된 Task가 있으면 `task.cancel()` 후 `True`, 없으면 `False`.
- `start()`에 `on_queued_cancel: Callable[[], Awaitable[None]] | None = None` 파라미터 추가, `_run()`에 전달.
- `_run()`을 `try: async with self._semaphore: await coro_factory() / except asyncio.CancelledError: (on_queued_cancel 있으면 호출) raise` 형태로 변경.
- 기존 `get_task()`는 그대로 둔다 — `backend/tests/unit/test_concurrency.py::test_get_task_returns_the_scheduled_task`가 이미 이 메서드를 테스트하고 있으므로 실사용 여부와 무관하게 건드리지 않는다.

**검증**: `backend/tests/unit/test_concurrency.py`(이미 존재, 여기에 추가):
- 세마포어를 다 채워서 두 번째 job이 대기하게 만든 뒤 `cancel()` 호출 → `on_queued_cancel`이 호출되는지, `coro_factory`는 실행되지 않는지.
- Task가 이미 실행 중일 때 `cancel()` 호출 → `on_queued_cancel`이 호출되지 않는지(coro_factory 내부의 취소 처리가 따로 있다고 가정하고, 여기서는 이중 호출 안 하는 것만 확인 — 실제 이중 호출 방지는 `_finalize_cancelled`의 멱등성이 담당하므로 이 레벨에서 굳이 막을 필요는 없음, 스펙 §JobManager._run 참고).
- 등록되지 않은 job_id로 `cancel()` → `False`.

## 3. `mvnrewrite/subprocess_runner.py` — 서브프로세스 강제 종료

- 기존 `except TimeoutError` 옆에 `except asyncio.CancelledError` 분기 추가: `proc.kill()` → `await proc.wait()` → `log_file`이 있으면 `"[강제종료됨]\n"` 기록 → `raise`(삼키지 않음).
- `finally`의 `log_file.close()`는 그대로 유지(취소 경로에서도 자연스럽게 실행됨).

**검증**: `backend/tests/unit/test_subprocess_runner.py`는 아직 없으므로 새로 생성한다(이 모듈을 직접 겨냥한 기존 테스트가 없다 — `grep -rln "run_subprocess" backend/tests`로 간접 커버리지가 있는 다른 테스트들의 mock 패턴을 먼저 참고해서 스타일을 맞춘다):
- 오래 걸리는 더미 커맨드(예: 크로스플랫폼하게 `python -c "import time; time.sleep(30)"`)를 `run_subprocess`로 감싼 `asyncio.Task`를 만들고 짧게 기다린 뒤 `task.cancel()` → `CancelledError`가 올라오는지, 프로세스가 실제로 죽었는지(`proc.returncode is not None` 등 확인 방법은 테스트 작성 시 구체화), `log_path`를 준 경우 파일에 `[강제종료됨]` 줄이 있는지.
- Windows/CI 양쪽에서 안정적으로 도는 더미 커맨드 선택에 주의.

## 4. `orchestration/pipeline.py` — `_finalize_cancelled` + 취소 처리 연결

- 신규 헬퍼 `async def _finalize_cancelled(job_id: str, settings: Settings, session_factory: sessionmaker[Session]) -> None` 추가:
  - `output_dir = settings.jobs_dir / job_id / "output"` (기존 `run_pipeline_resume_stage2`와 동일 패턴).
  - DB에서 현재 job을 조회해 이미 `TERMINAL_JOB_STATUSES`면 즉시 반환(멱등성 — 스펙 §JobManager._run 폴백 참고).
  - `set_job_status(session_factory, job_id, "cancelled")` 호출.
  - `emit_event(..., "status", {"status": "cancelled"})` 호출(기존 `make_emit_log`가 만드는 `emit`을 재사용할 수 있는지, 아니면 `streaming.events.emit_event`를 직접 쓸지는 기존 `set_job_status`/`emit` 호출부 패턴을 보고 맞춘다).
  - `output_dir.mkdir(parents=True, exist_ok=True)` 후 `(output_dir / "CANCELLED").write_text(...)`에 취소 시각(UTC ISO)을 기록.
- `run_pipeline`의 기존 `except IngestError` / `except Exception` 옆에 `except asyncio.CancelledError: await _finalize_cancelled(job_id, settings, session_factory); raise` 추가. **주의**: `except Exception` 블록보다 먼저 오든 나중에 오든 상관없다(`CancelledError`는 `Exception`을 상속하지 않으므로 서로 겹치지 않음) — 다만 가독성을 위해 `except IngestError` 바로 다음에 배치.
- `run_pipeline_resume_stage2`에도 동일하게 `except asyncio.CancelledError` 추가.
- `create_job`(`api/routers/jobs.py`, 다음 단계) 쪽에서 `manager.start(..., on_queued_cancel=lambda: _finalize_cancelled(job_id, settings, factory))`로 연결 — 이건 5단계에서 같이 처리.

**검증**: 새 단위 테스트(`backend/tests/unit/test_pipeline.py`에 추가) — 오래 걸리는 스텝을 흉내 내는 더미(`asyncio.sleep` 등)로 `run_pipeline`을 감싼 Task를 만들어 취소 → DB `status == "cancelled"`, `output/CANCELLED` 존재, 재취소해도(멱등성) 에러 없이 조용히 반환되는지.

## 5. `api/routers/jobs.py` — `POST /jobs/{id}/cancel` + `create_job` 연결

- `create_job`의 `manager.start(...)` 호출에 `on_queued_cancel=lambda: _finalize_cancelled(job_id, settings, factory)` 추가.
- 새 엔드포인트 `cancel_job` 추가(스펙 §API의 코드 그대로, `db.refresh(job)` 포함). 등록 위치는 `proceed_job` 근처.
- `models/job.py`에서 `TERMINAL_JOB_STATUSES` import 추가.

**검증**: `backend/tests/integration/test_jobs_api.py`에 추가:
- `running` 상태 job 취소 → 200, 이후(SSE 또는 재조회로) `cancelled` 확인.
- `awaiting_approval` 상태 job 취소 → 200, 즉시 `cancelled`(직접 마감 경로이므로 동기적으로 확정됨).
- 이미 터미널인 job 취소 → 409.
- 존재하지 않는 job_id → 404.
- `queued` 상태(세마포어 소진시켜 재현) 취소 → 200, `cancelled`로 확정.
- 이 과정에서 실제 mvn/git을 부르지 않도록, 기존 테스트들이 파이프라인을 어떻게 stub/mock하는지 `test_jobs_api.py`의 기존 fixture를 그대로 재사용한다(신규 mock 패턴 만들지 않음).

## 6. 프론트엔드

- `frontend/index.html`, `frontend/job.html`의 `progress-panel` 안, `proceed-btn` 바로 뒤에 `<button id="stop-btn" type="button" class="hidden">중지</button>` 추가.
- `frontend/assets/job-view.js`:
  - 상단 `const TERMINAL_STATUSES = new Set([...])`에 `"cancelled"` 추가.
  - `const stopBtn = el("stop-btn");` 참조 추가.
  - `showProceedButton`과 유사한 `showStopButton(jobId)` 함수 추가(또는 `status` 핸들러 안에서 인라인으로 처리 — 기존 `showProceedButton` 패턴을 따른다): 확인 다이얼로그 → 버튼 비활성화 + "중지 중..." → `POST /jobs/{id}/cancel` 호출 → 실패 시 에러 로그 + 버튼 원복.
  - `status` 이벤트 핸들러에서 터미널이 아니면 `stop-btn` 노출, 터미널이면 숨김(기존 `proceed-btn` 숨김 처리와 같은 자리에 나란히 추가).
- `frontend/assets/app.css`에 `.status-cancelled { background: var(--bg-sunken); color: var(--text-muted); }` 추가(`.status-queued`와 동일한 톤 — 기존 규칙 바로 아래 추가).
- `frontend/README.md`의 수동 스모크 테스트 체크리스트에 항목 추가(스펙 §테스트 계획의 "프론트엔드" 목록 그대로).

**검증**: 자동화된 프론트 테스트가 없는 프로젝트 방침(기존 `job-history-screen` 계획과 동일)이므로, 수동으로 백엔드+프론트를 띄워 실제 job을 하나 만들어 중지해보고 스펙 §테스트 계획의 프론트엔드 체크리스트를 전부 확인한다. `frontend/assets/*.js`는 `node --check`로 문법 오류만 우선 걸러낸다.

## 7. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인(0단계 베이스라인과 비교해 새로 깨진 테스트가 없는지).
- 실제로 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고, 샘플 프로젝트로 job을 하나 시작한 뒤:
  1. `running` 중 "중지" → 상태 배지가 `cancelled`로 바뀌는지, `backend/data/jobs/{id}/output/CANCELLED`가 생기는지, 그 시점 활성 로그 파일에 `[강제종료됨]` 줄이 남는지, 실제 `mvn`/`java` 프로세스가 OS에서 실제로 종료됐는지(작업 관리자/`tasklist`로 확인).
  2. `history.html → job.html?job={id}`로 진행 중인 job에 들어가 "중지"가 똑같이 동작하는지.
  3. 2단계 승인 대기(`awaiting_approval`) 상태를 만들어(1단계를 일부러 막히게 하거나 기존 job으로) 그 상태에서 "중지"가 정상 동작하는지.

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

스펙에 없던, 실제 코드를 보면서 정할 것:
- `_finalize_cancelled`가 쓸 SSE 이벤트 발행 방식이 `pipeline.py`의 기존 `make_emit_log`/`emit_event` 중 무엇과 가장 잘 맞는지 — `run_pipeline` 안에서는 이미 만들어진 `emit`(`make_emit_log`의 반환값)을 그대로 넘겨쓰면 되지만, `api/routers/jobs.py`의 `cancel_job`(작업 시작 전/`awaiting_approval` 직접 마감 경로)에는 그런 `emit`이 없으므로 `_finalize_cancelled`가 필요하면 내부에서 `make_emit_log`를 새로 만들어 쓴다.
