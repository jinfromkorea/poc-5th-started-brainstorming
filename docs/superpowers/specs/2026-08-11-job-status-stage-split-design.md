# Job 상태값 분리: `needs_handoff`를 Stage1/Stage2로 구분

## 배경 및 목적

지금 job 테이블의 최종 상태 `needs_handoff`는 Stage 1(스택 마이그레이션)이 막혀서 그런 건지, Stage 2(개별 CVE 패치)가 막혀서 그런 건지 DB에 구분되어 저장되지 않는다. 화면에서 job 하나가 `needs_handoff`로 끝나도 "어느 단계가 왜 막혔는지"를 알려면 `output/handoff/` 안의 파일 이름을 사람이 직접 봐야 한다.

이 문서는 `needs_handoff`를 `stage1_needs_handoff` / `stage2_needs_handoff` 두 개의 job 상태값으로 나눠서, DB만 봐도 어느 단계 문제인지 바로 알 수 있게 하는 설계다. 이 작업은 `docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md`(Stage 1 인수인계 후 재개 기능)의 전제 조건이기도 하다 — 그 스펙은 지금 `run_stage2=false`인 job만 다루도록 범위를 좁혀뒀는데, 이유가 바로 이 구분이 없어서였다. 이 기능이 들어가면 그 제약을 없앨 수 있다(§6).

## 범위

- job 테이블의 **최종(terminal) 상태값**만 다룬다. Stage1/Stage2 각 스텝·CVE 단위의 내부 결과 타입(`MigrationRunResult.status`, `StepOutcome.status`, `VulnOutcome.status` 등, 전부 `"success" | "needs_handoff"` 형태)은 이름 그대로 둔다 — 이건 job 상태가 아니라 파이프라인 내부에서 다음 스텝을 계속할지 결정하는 별개의 값이고, 이번 변경과 무관하다.
- 기존 로컬 DB에 이미 있는 `status="needs_handoff"` 행(현재 job 41, 44) 1회성 백필도 범위에 포함한다.
- 범위 밖: Stage1과 Stage2가 **한 번의 파이프라인 실행에서 동시에** 막히는 경우를 별도 상태값(예: `stage1_and_stage2_needs_handoff`)으로 남기는 것 — 사용자 결정에 따라 이 경우도 `stage2_needs_handoff`로 충분하다(§4.3).

## 결정 사항

- **새 상태값**: `needs_handoff`를 제거하고 `stage1_needs_handoff`, `stage2_needs_handoff` 두 개를 추가한다. 둘 다 `TERMINAL_JOB_STATUSES`에 포함.
- **`run_pipeline_resume_after_version_confirm`** 안에서는 Stage1 handoff와 Stage2 handoff가 같은 호출에서 동시에 발생하는 경로가 없다(Stage1이 막히면 `awaiting_stage2_approval` 게이트가 Stage2 진입 자체를 막으므로). 따라서 "어느 단계가 막았는지"를 그대로 추적하는 값 하나(`Literal["stage1", "stage2"] | None`)로 충분하다.
- **`run_pipeline_resume_stage2`**(`/proceed`로 재개되는 함수, Stage1이 이미 `needs_handoff`였던 경우에만 도달)는 지금 Stage2 결과와 무관하게 무조건 `needs_handoff`로 고정하는데, 이걸 실제 Stage2 결과를 반영하도록 고친다:
  - Stage2도 handoff → `stage2_needs_handoff`.
  - Stage2가 전부 성공(또는 대상 없음) → Stage1의 문제가 여전히 안 풀린 상태이므로 `stage1_needs_handoff`.
  - **Stage1과 Stage2가 둘 다 막힌 경우, 별도 값을 만들지 않고 `stage2_needs_handoff`로 남긴다.** Stage1 문제는 사람이 `/proceed`를 누른 시점에 이미 인지·승인한 것이고, `output/handoff/stage1-guide.md`도 그대로 남아있어 필요하면 볼 수 있다.
- **기존 데이터 백필**: 구현 시점에 `status="needs_handoff"`인 기존 행에 대해 1회성으로, `output/handoff/` 안에 `stage2-*-guide.md`가 하나라도 있으면 `stage2_needs_handoff`, 없고 `stage1-guide.md`만 있으면 `stage1_needs_handoff`로 갱신한다. 상시 로직이 아니라 이번 배포 때 한 번 실행하는 스크립트(또는 `init_db` 안의 일회성 코드 블록 — 실행 후 제거)로 처리한다. 현재 대상은 job 41(→ `stage2_needs_handoff`), job 44(→ `stage1_needs_handoff`) 두 건.

## 백엔드 설계

### `app/models/job.py`

```python
JOB_STATUSES = (
    "queued",
    "running",
    "awaiting_version_approval",
    "awaiting_approval",
    "success",
    "stage1_needs_handoff",
    "stage2_needs_handoff",
    "failed",
    "cancelled",
)
TERMINAL_JOB_STATUSES = frozenset({"success", "stage1_needs_handoff", "stage2_needs_handoff", "failed", "cancelled"})
```

주석(현재 `needs_handoff`를 설명하는 부분)도 두 값으로 나눠서 갱신.

### `app/orchestration/pipeline.py`

`run_pipeline_resume_after_version_confirm` (§279-401): 지금의 `needs_handoff: bool`을 아래로 대체.

```python
handoff_stage: Literal["stage1", "stage2"] | None = None
...
if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
    handoff_dir.mkdir(parents=True, exist_ok=True)
    (handoff_dir / "stage1-guide.md").write_text(stage1_result.handoff_guide, encoding="utf-8")
    handoff_stage = "stage1"
...
if run_stage2 and not awaiting_stage2_approval:
    stage2_report, stage2_needs_handoff = await _run_stage2_block(...)
    report_sections.append(stage2_report)
    if stage2_needs_handoff:
        handoff_stage = "stage2"
...
final_status = f"{handoff_stage}_needs_handoff" if handoff_stage else "success"
```

(`awaiting_stage2_approval` 판정 `run_stage1 and needs_handoff and run_stage2`는 `handoff_stage == "stage1" and run_stage2`로 바뀐다 — `run_stage2` 체크는 그대로 남아야 한다, 안 그러면 Stage2를 애초에 요청 안 한 job까지 `awaiting_approval`에 걸린다.)

`run_pipeline_resume_stage2` (§411-471): 무조건 `needs_handoff` 고정을 아래로 대체.

```python
stage2_report, stage2_needs_handoff = await _run_stage2_block(
    emit, log, job_id, work_dir, output_dir, stage_baseline, handoff_dir, settings, vulns
)
...
final_status = "stage2_needs_handoff" if stage2_needs_handoff else "stage1_needs_handoff"
```

### 기존 데이터 백필 (1회성)

```python
# scripts/backfill_needs_handoff_status.py (구현 후 실행하고 삭제 또는 보관)
import sqlite3
from pathlib import Path

conn = sqlite3.connect("backend/data/app.db")
for job_id, in conn.execute("SELECT id FROM jobs WHERE status = 'needs_handoff'"):
    handoff_dir = Path(f"backend/data/jobs/{job_id}/output/handoff")
    has_stage2 = any(handoff_dir.glob("stage2-*-guide.md"))
    new_status = "stage2_needs_handoff" if has_stage2 else "stage1_needs_handoff"
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
conn.commit()
```

## 프론트엔드 설계

- `frontend/assets/job-view.js`: `TERMINAL_STATUSES`에서 `"needs_handoff"`를 `"stage1_needs_handoff", "stage2_needs_handoff"`로 교체. 상태 텍스트를 사람이 읽는 문구로 매핑하는 곳이 있으면(`statusLabel` 류) 두 값 모두 자연스러운 한국어 문구로 추가(예: "1단계에서 인수인계 필요" / "2단계에서 인수인계 필요").
- `frontend/assets/app.css`: `.status-needs_handoff` 클래스를 `.status-stage1_needs_handoff`, `.status-stage2_needs_handoff` 두 개로 교체(같은 스타일 재사용 가능).
- `index.html`/`job.html`: 상태값을 하드코딩 비교하는 곳이 있으면 같이 갱신(구현 시 grep으로 재확인).

## 문서 반영

- `docs/architecture.md`, `docs/database.md`: `JOB_STATUSES` 설명 갱신.

## 테스트 계획

**단위/통합** (`tests/unit/test_pipeline.py`, `tests/integration/test_jobs_api.py`, `tests/integration/test_artifacts_api.py`의 기존 `needs_handoff` 관련 테스트를 갱신 + 아래 케이스 추가):

- `run_stage1`만 요청 + Stage1 handoff → `stage1_needs_handoff`.
- `run_stage1 + run_stage2`, Stage1 성공, Stage2 특정 CVE handoff → `stage2_needs_handoff`.
- `run_stage1 + run_stage2`, Stage1 handoff → `awaiting_approval`에서 멈추는지(기존과 동일, 회귀 확인).
- 그 뒤 `/proceed` → Stage2도 handoff → `stage2_needs_handoff`.
- 그 뒤 `/proceed` → Stage2가 전부 성공 → `stage1_needs_handoff`(Stage1 문제가 남아있다는 게 유지되는지).
- 백필 스크립트: 임시 DB + 임시 `output/handoff/` 디렉터리로 job 41/44에 해당하는 상황을 재현해 올바른 값으로 갱신되는지.

## §6. `stage1-handoff-resume-design.md`에 대한 후속 반영

이 기능이 구현되면 `docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md`를 다음과 같이 갱신한다(별도 커밋).

**핵심 근거**: `stage1_needs_handoff`는 오직 "더 이상 자동으로 진행될 게 없을 때"만 최종 상태로 붙는다. `run_stage2=true`인 job은 Stage1이 막히면 먼저 `awaiting_approval`(터미널 아님)에서 멈추고, 사람이 `/proceed`를 눌러야 Stage2가 실제로 돈다. 그 뒤 Stage2가 전부 성공해야 최종 상태가 `stage1_needs_handoff`가 된다(Stage2가 그 와중에도 막히면 `stage2_needs_handoff`가 되므로 애초에 이 케이스가 아니다). **즉 `stage1_needs_handoff`를 보고 있다는 것 자체가 "Stage2는 원래 없었거나, 이미 끝까지 성공했다"는 뜻이다.** 따라서 재개 기능은 `run_stage2` 값을 아예 확인할 필요 없이 게이트를 `job.status == "stage1_needs_handoff"` 하나로 단순화해도 안전하고, 재개 동작(나머지 Stage1 스텝만 이어서 실행) 자체도 바뀔 필요가 없다 — Stage2를 다시 트리거하는 로직은 추가하지 않는다.

구체적으로:

- 게이트를 `job.status == "needs_handoff" AND run_stage1 AND NOT run_stage2`에서 `job.status == "stage1_needs_handoff"`로 단순화.
- "범위" 섹션의 "Stage 1+2를 같이 선택한 job은 두 경우가 섞일 수 있어서 제외" 문구를 위 근거로 교체.
- 나머지 설계(백엔드 함수, 프론트엔드, 에러 처리, 테스트 계획)는 상태값 이름만 `needs_handoff` → `stage1_needs_handoff`로 바뀌고 구조는 그대로 유지.
