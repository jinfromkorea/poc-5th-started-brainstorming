# 구현 계획 — Job 상태 stage1/stage2 분리 + Stage 1 인수인계 후 재개

스펙:
- [`docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md`](../specs/2026-08-11-job-status-stage-split-design.md)
- [`docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md`](../specs/2026-08-11-stage1-handoff-resume-design.md)(§6 반영본 — `stage1_needs_handoff` 전제로 이미 갱신됨)

`writing-plans` 스킬이 이 환경에 없어 기존 계획 문서 형식(`2026-08-11-internal-parent-pom-target-version-plan.md`)을 따라 직접 작성한다. 두 스펙에 실제 코드가 거의 그대로 들어 있으므로 여기서는 반복하지 않고, 파일별 적용 순서·기존 코드와의 정확한 diff 지점·검증만 정리한다.

**순서가 중요하다**: 상태값 분리(1~4)가 먼저 끝나야 재개 기능(5~7)이 성립한다 — 재개 기능의 게이트 자체가 `stage1_needs_handoff`를 전제로 하기 때문. 프론트/문서(8~10)는 두 기능을 한 번에 반영한다.

## 0. 사전 확인

- `git status` 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 베이스라인 확인.
- 로컬 `backend/data/app.db`에 `status='needs_handoff'`인 행이 지금 job 41(`stage2_needs_handoff`가 될 것), job 44(`stage1_needs_handoff`가 될 것) 2건뿐임을 재확인(스펙 작성 시점에 이미 확인함 — 구현 중 job이 더 생겼을 수 있으니 다시 확인).

## 1. `app/models/job.py` — 상태값 분리

```python
JOB_STATUSES = (
    "queued", "running", "awaiting_version_approval", "awaiting_approval",
    "success", "stage1_needs_handoff", "stage2_needs_handoff", "failed", "cancelled",
)
TERMINAL_JOB_STATUSES = frozenset(
    {"success", "stage1_needs_handoff", "stage2_needs_handoff", "failed", "cancelled"}
)
```

주석(§17-37, `needs_handoff`를 설명하는 부분)도 두 값으로 나눠 갱신. `cancel_job`(jobs.py)의 `TERMINAL_JOB_STATUSES` 사용, `delete_job`의 사용은 프로즌셋 이름 그대로라 코드 변경 불필요.

**검증**: 이 시점에선 아직 아무 코드도 새 값을 쓰지 않으므로 테스트 실행 불필요 — 다음 스텝과 묶어서 검증.

## 2. `app/orchestration/pipeline.py` — 두 resume 함수의 상태 계산 로직

### `run_pipeline_resume_after_version_confirm` (§279-401)

`needs_handoff: bool` 지역변수를 제거하고 `handoff_stage: Literal["stage1", "stage2"] | None = None`로 대체(파일 상단에 `from typing import Literal` 추가 필요 여부 확인 — 없으면 추가).

- `stage1_result.status == "needs_handoff"` 분기(§346): `needs_handoff = True` → `handoff_stage = "stage1"`.
- `awaiting_stage2_approval = run_stage1 and needs_handoff and run_stage2`(§374) → `awaiting_stage2_approval = handoff_stage == "stage1" and run_stage2`.
- `if run_stage2 and not needs_handoff:`(§362, post-stage1 스캔 결과를 stage2 대상으로 넘길지) → `if run_stage2 and handoff_stage is None:`.
- `if run_stage2 and not awaiting_stage2_approval:`(§376) 그대로 유지(불리언 값 자체는 안 바뀜).
- `_run_stage2_block` 리턴값 `stage2_needs_handoff`(§377) 소비 부분(§381 `needs_handoff = needs_handoff or stage2_needs_handoff`) → `if stage2_needs_handoff: handoff_stage = "stage2"`.
- 최종(§399): `final_status = "needs_handoff" if needs_handoff else "success"` → `final_status = f"{handoff_stage}_needs_handoff" if handoff_stage else "success"`.

### `run_pipeline_resume_stage2` (§411-471)

§461의 무조건 고정을 실제 Stage2 결과 기반으로 변경:

```python
stage2_report, stage2_needs_handoff = await _run_stage2_block(
    emit, log, job_id, work_dir, output_dir, stage_baseline, handoff_dir, settings, vulns
)
...
final_status = "stage2_needs_handoff" if stage2_needs_handoff else "stage1_needs_handoff"
```

기존 주석(§458-460, "이 resume 경로는 Stage1이 이미 needs_handoff로 끝났기 때문에만 돈다")은 유지하되, "그래서 결과가 어떻든 최종 상태는 needs_handoff로 고정한다"는 뒷부분만 위 로직 설명으로 교체.

**검증**: `backend/tests/unit/test_pipeline.py`의 기존 `needs_handoff` 관련 테스트를 `stage1_needs_handoff`/`stage2_needs_handoff`로 갱신(아래 표대로) + 새 케이스 추가:
- 1단계만 요청 + 1단계 handoff → `stage1_needs_handoff`.
- 1+2단계, 1단계 성공, 2단계 특정 CVE handoff → `stage2_needs_handoff`.
- 1+2단계, 1단계 handoff → `awaiting_approval`(회귀, 기존과 동일).
- 그 뒤 `/proceed` 재개, 2단계도 handoff → `stage2_needs_handoff`.
- 그 뒤 `/proceed` 재개, 2단계 전부 성공 → `stage1_needs_handoff`(기존엔 이 case가 무조건 `needs_handoff`였던 걸 고치는 것 — 회귀 아님, 의도된 동작 변경이므로 기존 테스트가 이 case를 `needs_handoff`로 assert하고 있었다면 그 assert 자체를 고쳐야 함).

## 3. 나머지 코드에서 `needs_handoff`(job-level) 참조 정리

`grep -rn "needs_handoff" backend/app`으로 재확인 후, **job 테이블 상태값으로 쓰인 곳만** 골라 수정(스텝/CVE 단위 내부 리터럴 `MigrationRunResult.status`, `StepOutcome.status`, `VulnOutcome.status`, `RunStatus`/`StepStatus`/`VulnStatus` `Literal` 타입은 이름 그대로 둔다 — §2에서 이미 확인했듯 job 상태와 무관).

- `api/routers/jobs.py`: 지금은 `needs_handoff`를 직접 비교하는 곳이 없음(§4에서 만들 `resume-stage1`이 처음). 그대로.
- `reporting/report_builder.py`의 `StepStatus`: 스텝 단위라 변경 없음(§2에서 확인).

## 4. 기존 데이터 백필 (1회성)

`backend/scripts/backfill_needs_handoff_status.py` 새로 작성(1회 실행 후 삭제하지 않고 남겨둠 — 향후 유사한 로컬 DB 복원 상황에 재사용 가능하므로):

```python
"""1회성: needs_handoff -> stage1_needs_handoff/stage2_needs_handoff 백필
(spec: docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md).
Run once after the JOB_STATUSES change lands, from backend/: 
  .venv312/Scripts/python.exe scripts/backfill_needs_handoff_status.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"
JOBS_DIR = Path(__file__).parent.parent / "data" / "jobs"

conn = sqlite3.connect(DB_PATH)
rows = conn.execute("SELECT id FROM jobs WHERE status = 'needs_handoff'").fetchall()
for (job_id,) in rows:
    handoff_dir = JOBS_DIR / job_id / "output" / "handoff"
    has_stage2 = any(handoff_dir.glob("stage2-*-guide.md"))
    new_status = "stage2_needs_handoff" if has_stage2 else "stage1_needs_handoff"
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
    print(f"job {job_id}: needs_handoff -> {new_status}")
conn.commit()
conn.close()
```

**검증**: 실행 후 `SELECT id, status FROM jobs WHERE id IN ('41', '44')`로 41→`stage2_needs_handoff`, 44→`stage1_needs_handoff` 확인.

## 5. `app/mvnrewrite/mvn_client.py` — 이미 존재, 변경 없음

`mvn_test_compile`이 이미 있다(§7.2에서 이미 씀). 그대로 재사용.

## 6. `app/orchestration/multi_step.py` — `verify_after_manual_fix` 추가

스펙 코드 그대로 파일 끝(또는 `run_stage1_migration` 아래)에 추가:

```python
from app.mvnrewrite.mvn_client import mvn_test_compile  # 상단 import에 추가

async def verify_after_manual_fix(work_dir: Path, settings: Settings, on_log: LogFn = noop_log) -> tuple[bool, str]:
    await on_log("인수인계 후 수동 수정 확인 중 (mvn test-compile)")
    result = await mvn_test_compile(work_dir, settings)
    await on_log(f"검증 {'통과' if result.returncode == 0 else '실패'}")
    return result.returncode == 0, result.output
```

**검증**: `backend/tests/unit/test_multi_step.py`에 추가 — `mvn_test_compile`을 monkeypatch해서 성공/실패 각각 `(True/False, output)` 반환하는지.

## 7. `app/orchestration/pipeline.py` — `run_pipeline_resume_stage1_after_handoff`

스펙 코드 그대로 추가(§2에서 이미 `handoff_stage`/`Literal` 패턴을 쓰고 있으므로 import는 이미 있을 `Literal`만 재사용). 정확한 배치는 `run_pipeline_resume_stage2` 바로 아래.

- import 추가: `from app.orchestration.multi_step import TARGET_STACK_SUMMARY, run_stage1_migration, verify_after_manual_fix`, `from app.handoff.guide_builder import build_handoff_guide`(이미 pipeline.py에 없다면 — `_run_stage2_block` 등에서 이미 쓰고 있을 가능성 높음, 확인 후 중복 import 방지).
- 실패 경로: `set_job_status(..., "stage1_needs_handoff")`.
- 성공 경로: `run_stage1_migration` 재실행 후 `final_status = "stage1_needs_handoff" if stage1_result.status == "needs_handoff" else "success"`, 낡은 `stage1-guide.md` 삭제 로직 포함.

**검증**: `backend/tests/unit/test_pipeline.py`에 추가(기존 monkeypatch 패턴 재사용):
- `verify_after_manual_fix` 실패 mock → 상태 `stage1_needs_handoff` 유지, `mvn_effective_pom`/`run_stage1_migration` 미호출.
- `verify_after_manual_fix` 성공 + `run_stage1_migration`(나머지 계획 없음, 바로 성공) mock → `success`, `report_markdown`에 이전+새 리포트 모두 포함, 1차 시도 `stage1-guide.md` 삭제됨.
- `verify_after_manual_fix` 성공 + `run_stage1_migration`이 다음 스텝에서 또 막힘(mock) → `stage1_needs_handoff`, 새 가이드 파일 갱신됨.

## 8. `app/api/routers/jobs.py` — `POST /jobs/{id}/resume-stage1`

스펙 코드 그대로, `proceed_job` 아래에 추가:

```python
@router.post("/{job_id}/resume-stage1", response_model=JobCreateResponse)
async def resume_stage1(
    job_id: str, settings: Settings = Depends(get_settings), db=Depends(get_db_session),
) -> JobCreateResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status != "stage1_needs_handoff":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not resumable (status={job.status})",
        )
    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)
    manager.start(job_id, lambda: run_pipeline_resume_stage1_after_handoff(job_id, settings, factory))
    return JobCreateResponse(job_id=job_id, status="running")
```

import 추가: `run_pipeline_resume_stage1_after_handoff`(기존 `run_pipeline_resume_after_version_confirm, run_pipeline_resume_stage2` 옆).

**검증**: `backend/tests/integration/test_jobs_api.py`에 추가(기존 `app_client`/monkeypatch 패턴 재사용):
- `test_resume_stage1_rejected_when_not_stage1_needs_handoff` — 여러 상태(`queued`, `running`, `success`, `stage2_needs_handoff`)에서 409.
- `test_resume_stage1_allowed_when_run_stage2_true` — `run_stage1=true, run_stage2=true`로 만든 job을 강제로 `stage1_needs_handoff`로 세팅해도 정상 스케줄링되는지(§범위의 핵심 근거 회귀 테스트).
- 404(존재하지 않는 job_id).

## 9. 프론트엔드

### `assets/job-view.js`

- `TERMINAL_STATUSES`: `"needs_handoff"` → `"stage1_needs_handoff", "stage2_needs_handoff"`.
- 엘리먼트 참조 추가: `resumeStage1Btn = el("resume-stage1-btn")`.
- 새 함수 `showResumeStage1Button(jobId)`(기존 `showProceedButton`과 동일한 모양 — 클릭 시 `POST /jobs/{id}/resume-stage1`, 성공하면 버튼 숨기고 SSE가 닫혀 있었으면(터미널 상태였으므로 이미 닫혀 있음) `connectSSE(jobId)` 재호출).
- `status` 이벤트 핸들러(§452 부근)에 추가: `if (data.status === "stage1_needs_handoff") { showResumeStage1Button(jobId); } else { resumeStage1Btn.classList.add("hidden"); }`. `TERMINAL_STATUSES.has(data.status)`이면 `es.close()`가 이미 실행되므로, 재개 버튼 클릭 시 SSE 재연결이 필요함에 유의(§ 위).

### `index.html`, `job.html` (둘 다 동일하게)

`handoff-list` 바로 아래에 추가:

```html
<button type="button" id="resume-stage1-btn" class="hidden secondary">인수인계 후 재개 (수동 수정 확인)</button>
```

### `assets/app.css`

`.status-needs_handoff`(§433) → `.status-stage1_needs_handoff`, `.status-stage2_needs_handoff` 두 개로 교체(같은 `warning` 스타일 재사용).

**검증**: `node --check frontend/assets/job-view.js`.

## 10. 문서 반영

- `docs/architecture.md`:
  - §4 다이어그램(§128) `status=success|needs_handoff|failed` → `status=success|stage1_needs_handoff|stage2_needs_handoff|failed`.
  - §4 순서 설명(§146) `success` / `needs_handoff` / `failed` → 세 값으로.
  - §7.4 상태 다이어그램(§233-246)과 그 아래 설명(§248-250) 갱신 — 특히 §250 "2단계까지 재개된 job은... 최종 상태가 항상 `needs_handoff`다"는 §2에서 고친 실제 동작과 모순되므로 삭제하고, 새 분기(`stage2_needs_handoff` vs `stage1_needs_handoff`)로 교체.
  - 새 §7.6 "Stage 1 인수인계 후 재개" 절 추가 — `stage1-handoff-resume-design.md` 요약(게이트, `verify_after_manual_fix`, 재계획 방식, `POST /jobs/{id}/resume-stage1`).
- `docs/database.md` §56: `JOB_STATUSES`/`TERMINAL_JOB_STATUSES` 나열값 갱신.
- `frontend/README.md`: 기존 체크리스트 근처에 항목 추가 —
  - Stage1만 요청한 job이 `needs_handoff`로 끝나면 "인수인계 후 재개" 버튼이 뜨는지(Stage2 요청 job은 §범위대로 `stage1_needs_handoff`에 도달했을 때만 — 즉 Stage2까지 이미 끝난 뒤).
  - `work/`를 직접 고친 뒤 버튼 클릭 → 검증 통과 시 나머지 계획이 이어서 실행되고 최종 성공하면 handoff 가이드 버튼이 사라지는지.
  - 검증 실패 시 버튼이 다시 나타나고 가이드 내용이 최신 실패로 갱신되는지.

## 11. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` 전체 통과.
- `backend/data/app.db`에 백필 스크립트 실행, 41/44 상태 확인(§4).
- 실제 서버(`uvicorn`) + 정적 프론트로 job 44에서:
  1. `job.html?job=44` 접속 → 상태 배지가 `stage1_needs_handoff`, "인수인계 후 재개" 버튼이 보이는지.
  2. `backend/data/jobs/44/work/`가 이미 수동으로 고쳐진 상태(이전 세션에서 완료)이므로 바로 버튼 클릭 → 검증 통과 → 나머지 계획(있다면) 진행 → 최종 상태 확인, `output/handoff/stage1-guide.md`가 성공 시 삭제됐는지.
  3. (선택) 검증이 실패하도록 `work/`를 일부러 깨뜨린 job으로 같은 버튼을 눌러 실패 경로(상태 유지, 가이드 갱신, 버튼 재노출) 확인.

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

- `Literal` import가 `pipeline.py` 상단에 이미 있는지 확인(§2) — 없으면 `from typing import Literal` 추가.
- §7의 `build_handoff_guide` import가 `pipeline.py`에 이미 있는지 확인(다른 handoff 관련 코드가 이미 이 모듈을 쓰고 있을 가능성) — 있으면 중복 추가하지 않는다.
- 백필 스크립트(§4)의 정확한 실행 시점: §1~3(코드 변경) 배포 직후, §11 전체 검증 전에 한 번 실행 — 순서를 놓치면 job 41/44가 `JOB_STATUSES`에 없는 고아 문자열 상태로 남아 `TERMINAL_JOB_STATUSES` 판정(`cancel_job`, `delete_job`)에서 예상과 다르게 동작할 수 있다(둘 다 "그 값이 집합에 있는지"만 보므로 실제로는 에러 없이 "터미널 아님"으로 취급돼 삭제/취소가 막히는 정도 — 심각하지 않지만 사용자가 job 44를 재개하려면 어차피 필요).
