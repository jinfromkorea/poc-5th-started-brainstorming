# 작업 강제 중지 (Job Cancellation)

## 배경 및 목적

현재 job은 시작(`POST /jobs`)한 뒤 `success`/`needs_handoff`/`failed`로 끝나거나, `awaiting_approval`에서 사람의 승인(`POST /jobs/{id}/proceed`)을 기다릴 뿐, 진행 중인 job을 사용자가 스스로 멈출 방법이 없다. 잘못된 입력으로 job을 시작했거나, 예상보다 오래 걸리는 걸 보고 그만두고 싶을 때도 job이 끝날 때까지(또는 타임아웃까지) 기다리는 수밖에 없다.

이 문서는 프론트엔드의 "새 작업 시작" 이후 "진행 상황" 화면에서 실행 중인 job을 즉시 강제 종료할 수 있는 "중지" 기능을 추가하는 설계를 다룬다.

## 범위

- 백엔드: `POST /jobs/{id}/cancel` 엔드포인트, 새 job 상태 `cancelled`, 서브프로세스/파이프라인 취소 전파, 취소 식별 마커
- 프론트엔드: `index.html`/`job.html`의 "진행 상황" 패널에 "중지" 버튼 추가 (두 페이지가 공유하는 `job-view.js`에 로직 작성)

범위 밖: 중지된 job의 재개/재시도(기존에도 이런 개념이 없으므로 일관성 유지 — 다시 하려면 새 job을 만든다), 중지 시점까지의 diff/report 산출물 생성(아래 근거 참고).

## 취소 방식 결정 사항

- **즉시 강제 종료**: 지금 떠 있는 mvn/git/Trivy 서브프로세스나 AI 호출을 그 자리에서 kill한다. 다음 안전 지점까지 기다리는 협조적(cooperative) 취소는 채택하지 않는다.
- **취소 가능한 상태**: `queued` / `running` / `awaiting_approval` 전부. 터미널 상태(`success`/`needs_handoff`/`failed`/`cancelled`)는 대상이 아니다.
- **결과물(diff/report) 생성 안 함**: 취소된 job은 `output/patch.diff`, `output/report.md`를 만들지 않는다. `work/`는 중단된 상태 그대로 남는다.
- **강제종료 식별**: 화면(상태 배지)과 `backend/data/jobs/{id}/` 폴더(마커 파일 + 로그 종료 줄) 양쪽에서 모두 알아볼 수 있어야 한다.

## 구현 접근 방식

job은 이미 `orchestration/concurrency.JobManager`가 `asyncio.Task`로 관리하고 있다. 이 Task를 `task.cancel()`로 취소하면, 지금 `await` 중인 지점(서브프로세스 출력 읽기, AI 호출 등)에서 `asyncio.CancelledError`가 즉시 발생한다. 이를 서브프로세스 실행 지점에서 잡아 `proc.kill()`로 실제 OS 프로세스까지 죽인 뒤 다시 던지는 방식을 쓴다 — 이미 있는 타임아웃 처리(`subprocess_runner.py`의 `except TimeoutError: proc.kill()`)와 구조가 동일해서 자연스럽게 확장할 수 있고, 취소된 Task를 별도로 추적할 필요가 없어 PID를 직접 관리하는 방식보다 단순하고 안전하다.

`asyncio.CancelledError`는 (Python 3.8+) `Exception`이 아니라 `BaseException`을 상속하므로, `pipeline.py`의 기존 `except Exception`(→ `failed` 처리) 블록이 이를 실수로 가로챌 걱정은 없다.

## 상태 모델 & API

### `models/job.py`

```python
JOB_STATUSES = ("queued", "running", "awaiting_approval", "success", "needs_handoff", "failed", "cancelled")
TERMINAL_JOB_STATUSES = frozenset({"success", "needs_handoff", "failed", "cancelled"})
```

### `orchestration/concurrency.py` — `JobManager.cancel`

```python
def cancel(self, job_id: str) -> bool:
    """task.cancel()만 호출한다 -- 실제 정리(프로세스 kill, DB 상태 반영,
    마커 파일 작성)는 취소된 코루틴 자신(run_pipeline 또는 _run의 폴백)이
    한다. 대상 Task가 없으면(서버 재시작 등으로 잃어버린 경우) False."""
    task = self._tasks.get(job_id)
    if task is None:
        return False
    task.cancel()
    return True
```

### `api/routers/jobs.py` — `POST /jobs/{id}/cancel`

```python
@router.post("/{job_id}/cancel", response_model=JobCreateResponse)
async def cancel_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> JobCreateResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is already terminal (status={job.status})",
        )

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)

    if job.status == "awaiting_approval":
        # run_pipeline이 이미 return으로 끝난 뒤라 살아있는 Task가 없다 --
        # 죽일 프로세스도 없으므로 여기서 직접 마감한다.
        await _finalize_cancelled(job_id, settings, factory)
    elif not manager.cancel(job_id):
        # DB엔 running/queued인데 매니저가 Task를 잃어버린 경우(서버 재시작 등)
        # -- 정리할 프로세스가 실제로 없으므로 상태만 바로잡는다.
        await _finalize_cancelled(job_id, settings, factory)
    # else: task.cancel()이 성공적으로 호출됨 -- 실제 DB 반영/마커 작성은
    # run_pipeline의 except asyncio.CancelledError (또는 JobManager._run의
    # 폴백)이 비동기로 처리하고, 그 결과는 이미 열려 있는 SSE로 전달된다.

    db.refresh(job)
    return JobCreateResponse(job_id=job_id, status=job.status)
```

`_finalize_cancelled(job_id, settings, session_factory)`(신규 헬퍼, `pipeline.py`에 위치 — `run_pipeline`의 취소 처리와 로직을 공유)는 DB 상태를 `cancelled`로 바꾸고 `status` SSE 이벤트를 쏘고 `output/CANCELLED` 마커를 쓴다. `output_dir`는 별도로 받지 않고 `run_pipeline_resume_stage2`가 이미 쓰는 것과 같은 패턴(`settings.jobs_dir / job_id / "output"`)으로 내부에서 계산한다 — job_id만 있으면 어느 경로에서 호출하든 항상 같은 결과이므로 모든 호출부(§API, §pipeline.py, §queued 폴백)가 인자 없이 동일하게 쓸 수 있다. 서브프로세스가 없는 경로(직접 마감)이므로 로그 종료 줄 작성은 해당 없음 — 마커 파일만 남는다.

응답의 `status`는 두 직접-마감 경로(`awaiting_approval`, Task를 잃어버린 경우)에서는 이미 확정된 `cancelled`를 정직하게 반환하지만, `task.cancel()`만 호출한 경로에서는 아직 DB가 반영되기 전이라 직전 상태(`running`/`queued`)가 그대로 나올 수 있다 — 이 엔드포인트는 기존 `/proceed`와 마찬가지로 "요청 접수"의 의미이고, 실제 확정된 상태는 프론트가 이미 열려 있는 SSE의 `status` 이벤트로 받는다(§프론트엔드).

## 취소 전파 메커니즘

job이 취소되는 시점의 실행 단계에 따라 실제 정리 주체가 다르다.

### `subprocess_runner.run_subprocess` — 서브프로세스 강제 종료

기존 타임아웃 처리 옆에 취소 처리를 추가한다:

```python
try:
    await asyncio.wait_for(_pump(), timeout=timeout)
    returncode = await asyncio.wait_for(proc.wait(), timeout=5)
except TimeoutError as exc:
    proc.kill()
    await proc.wait()
    raise SubprocessTimeoutError(...) from exc
except asyncio.CancelledError:
    proc.kill()
    await proc.wait()
    if log_file is not None:
        log_file.write("[강제종료됨]\n")
        log_file.flush()
    raise  # 취소를 삼키지 않고 그대로 전파 -- Task가 실제로 cancelled로 끝나야 함
finally:
    if log_file is not None:
        log_file.close()
```

AI 호출(`agent.ainvoke`) 도중 취소된 경우는 별도 처리가 필요 없다 — asyncio가 진행 중인 HTTP 요청을 알아서 취소한다. 다만 이 경우 그 호출의 로그 파일(`output/logs/{stage}/llm/*.md`)은 응답 도착 후에나 쓰이므로 아예 생성되지 않는다 — 자연스러운 한계로 두고, `output/CANCELLED` 마커가 그 자리를 대신한다.

### `orchestration/pipeline.py` — `run_pipeline` / `run_pipeline_resume_stage2`

기존 `except IngestError`/`except Exception` 옆에 취소 처리를 추가한다:

```python
except asyncio.CancelledError:
    await _finalize_cancelled(job_id, settings, session_factory)
    raise
```

### `orchestration/concurrency.py` — `JobManager._run`의 폴백

`queued` 상태에서 취소되면 세마포어 획득 대기 중(`async with self._semaphore:`)에 `CancelledError`가 발생해 `coro_factory()`(= `run_pipeline`) 자체가 아예 시작되지 않는다 — 이 경우 `run_pipeline`의 예외 처리기는 실행될 기회조차 없으므로 DB에 `cancelled`를 남길 주체가 없다.

`JobManager`는 지금도 DB/`Settings`를 전혀 모르는(세마포어 + Task 추적만 하는) 순수한 동시성 유틸리티다 — 이 책임을 유지하기 위해, DB 접근이 필요한 마감 처리 자체를 `JobManager`에 넣지 않고 `start()` 호출 시 "취소되면 실행할 콜백"을 함께 등록받는 방식으로 처리한다:

```python
def start(
    self,
    job_id: str,
    coro_factory: Callable[[], Awaitable[None]],
    on_queued_cancel: Callable[[], Awaitable[None]] | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(self._run(coro_factory, on_queued_cancel))
    self._tasks[job_id] = task
    return task

async def _run(
    self, coro_factory: Callable[[], Awaitable[None]], on_queued_cancel: Callable[[], Awaitable[None]] | None
) -> None:
    try:
        async with self._semaphore:
            await coro_factory()
    except asyncio.CancelledError:
        if on_queued_cancel is not None:
            # coro_factory()가 아예 호출되기 전(세마포어 대기 중) 취소된 경우에만
            # 의미 있다 -- coro_factory()가 이미 실행 중이었다면 그 안의
            # except asyncio.CancelledError가 먼저 처리하고 다시 raise하므로,
            # 그 CancelledError가 여기 다시 걸릴 때는 이미 DB가 cancelled로
            # 반영된 뒤다. on_queued_cancel 자체를 멱등하게 만들어(이미
            # cancelled면 아무 것도 안 함) 이중 처리를 걱정하지 않아도 되게 한다.
            await on_queued_cancel()
        raise
```

`api/routers/jobs.py`의 `create_job`은 `manager.start(job_id, lambda: run_pipeline(...), on_queued_cancel=lambda: _finalize_cancelled(job_id, settings, factory))`처럼 호출한다. `_finalize_cancelled`는 호출 전에 현재 DB 상태를 확인해 이미 터미널이면 아무 것도 하지 않도록 멱등하게 구현한다 — `_run`의 `except`가 "세마포어 대기 중 취소"와 "`coro_factory()` 내부에서 이미 처리되고 다시 올라온 취소"를 구분하지 못해도 안전하도록.

## 프론트엔드

### `index.html`, `job.html`의 `progress-panel`

두 페이지 모두 `proceed-btn` 옆에 동일하게 추가(기존 `proceed-btn`이 두 파일에 중복 존재하는 것과 같은 패턴):

```html
<button id="stop-btn" type="button" class="hidden">중지</button>
```

### `assets/job-view.js`

- `status` SSE 이벤트 핸들러에서: 상태가 터미널이 아니면(`queued`/`running`/`awaiting_approval`) `stop-btn` 표시, 터미널이면(`cancelled` 포함) 숨김.
- 클릭 핸들러: `confirm("정말 이 작업을 중지할까요? 지금까지의 변경 내용은 저장되지 않습니다.")` → 확인 시 버튼 비활성화 + "중지 중..." 표시 → `POST /jobs/{id}/cancel` 호출. 이후 상태 반영은 이미 열려 있는 SSE의 `status` 이벤트가 처리한다(`proceed-btn`과 동일 패턴, 별도 폴링 없음).

### `assets/app.css`

```css
.status-cancelled {
  background: var(--bg-sunken);
  color: var(--text-muted);
}
```

`failed`(빨강)와 구분되는 중립 톤 — 에러가 아니라 사용자가 스스로 멈춘 것이므로. `history.js`는 `status-${job.status}` 클래스를 이미 범용으로 적용하므로 이 CSS 규칙만 추가하면 이력 목록 테이블에도 자동 반영된다(코드 수정 불필요).

## 에러 처리 / 엣지 케이스

- 이미 터미널인 job에 취소 요청 → 409.
- 존재하지 않는 job_id → 404.
- 서버 재시작으로 `JobManager`가 Task를 잃어버린 경우(DB는 `running`/`queued`인데 실제 프로세스가 없음) → `manager.cancel()`이 `False`를 반환하면 엔드포인트가 직접 DB를 정정.
- 중복 취소 요청 → 두 번째 요청 시점엔 이미 `cancelled`(터미널)이므로 409로 막힘.
- `awaiting_approval`에서 취소 → 살아있는 Task가 없으므로 `manager.cancel()`을 아예 호출하지 않고 엔드포인트가 직접 마감.

## 테스트 계획

**단위**:
- `subprocess_runner.run_subprocess`: 오래 걸리는 더미 커맨드를 감싼 Task를 취소했을 때 실제로 `proc.kill()`이 호출되고, `log_path`가 주어졌으면 로그 파일에 `[강제종료됨]` 줄이 남는지.
- `JobManager.cancel()`: 등록된 Task가 있을 때/없을 때 각각 반환값과 `task.cancel()` 호출 여부.
- `pipeline.py`의 `except asyncio.CancelledError` 경로: `run_pipeline`을 감싼 Task를 취소했을 때 DB 상태가 `cancelled`로, `output/CANCELLED`가 생성되는지 (mock/더미 서브프로세스로 검증).

**통합** (`backend/tests/integration/test_jobs_api.py`에 추가):
- `queued`/`running`/`awaiting_approval` 각 상태에서 `POST /jobs/{id}/cancel` → 200(`/proceed`와 동일한 기존 컨벤션), 최종 DB 상태 `cancelled`.
- 터미널 상태에서 취소 요청 → 409.
- 존재하지 않는 job_id → 404.

**프론트엔드** (수동 스모크 테스트, `frontend/README.md` 체크리스트에 추가):
- `index.html`에서 job 시작 직후 "중지" 클릭 → 확인 다이얼로그 → 상태 배지가 `cancelled`로 바뀌고 로그에 남는지.
- `history.html` → `job.html?job={id}`로 진입한, 아직 실행 중인 job에서도 "중지" 버튼이 보이고 정상 동작하는지.
- 이미 끝난 job의 `job.html` 상세 페이지에는 "중지" 버튼이 보이지 않는지.
- `backend/data/jobs/{id}/output/CANCELLED` 마커 파일과, 취소 시점에 실행 중이던 로그 파일의 종료 줄을 직접 확인.
