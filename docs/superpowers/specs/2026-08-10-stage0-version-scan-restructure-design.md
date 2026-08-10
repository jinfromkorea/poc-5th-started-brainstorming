# Stage 0 도입 — 출력 버전 자동 계산 + HITL 확인 + 취약점 스캔 재배치

## 배경 및 목적

`2026-08-06-oss-dependency-governance-design.md`는 "출력 아티팩트 버전은 도구가 임의로 추론/증가시키지 않는다 — 사람이 직접 입력한다"고 명시했었다. 이 문서는 그 결정을 **의도적으로 뒤집는다**: 사내 Nexus에 배포될 때 기존 버전을 덮어쓰는 사고를 막으려면, 출력 버전은 항상 현재 버전과 달라야 한다는 게 더 중요한 요구사항으로 확인됐다. 이를 강제하려면 사람 입력에만 맡길 수 없고, 도구가 계산해서 제안하고 사람이 확인하는 방식이 필요하다.

같은 계기로, 취약점 스캔이 지금 여러 지점에서 흩어져 실행되는 것도 정리한다: 1단계 시작 시 "마이그레이션 전" 스캔, 2단계 시작 시 "패치 대상 선정" 스캔이 있는데, 1단계가 실제로 실행되면 그 사이에 상태가 안 바뀐 `work/`를 두 번 스캔하는 셈이라 낭비다.

이번 작업은 `2026-08-10-output-version-suggestion-design.md`(Part A, 제출 전 수동 확인 버튼)를 **대체**한다 — Part A의 UI/엔드포인트는 삭제하고, 그 자리에 이 문서의 Stage 0가 들어간다.

## 범위

- 백엔드: 파이프라인에 새 단계("Stage 0") 도입, 새 job 상태 `awaiting_version_approval`, 새 엔드포인트 `POST /jobs/{id}/confirm-version`, 취약점 스캔 체크포인트 재배치(3곳: 베이스라인/1단계 후/2단계 후), 버전 자동 계산 로직
- 프론트엔드: `index.html`의 수동 "출력 아티팩트 버전" 입력/확인 버튼 제거, `index.html`+`job.html` 공통 진행 패널에 버전 확인 UI 추가, "분석" 패널에 3번째 취약점 표(최종) 추가

범위 밖: 스택 일치 여부를 화면에 별도 배지로 표시하는 것(기존 "분석" 패널의 감지된 스택 표시로 충분하다고 판단), Spring Cloud 개별 목표 트레인 계산(기존 `planning.py` 로직 그대로 사용, 이번 변경과 무관).

## 결정 사항

- **출력 버전은 항상 사람이 입력하지 않고 도구가 계산한다.** `index.html`의 수동 필드와 "현재 버전 확인" 버튼, `POST /inspect/artifact-version` 엔드포인트는 삭제한다. `POST /jobs`는 더 이상 `output_version`을 받지 않는다.
- **계산 기준은 1단계 실행 여부뿐이다.** "스택이 이미 목표와 같은지"는 계산에 넣지 않는다 — 1단계를 켰다는 것 자체가 "이번 릴리스는 메이저"라는 의도로 보고, 껐으면 스택 상태와 무관하게 "이번 릴리스는 마이너"로 본다.
  - 1단계 포함 → MAJOR 증가 (예: `1.1.1` → `2.0.0`)
  - 1단계 미포함 → MINOR 증가 (예: `1.0.0` → `1.1.0`)
  - 감지된 현재 버전이 `MAJOR.MINOR.PATCH` 형태로 파싱 안 되면(예: `-RC1` 같은 다른 접미사, 4단 버전) 증가시키지 않고 정규화만 된 값을 그대로 제안 — 잘못 계산하는 것보다 사람이 직접 고치게 하는 게 낫다.
  - "사용되지 않는 기술 스택 제외" 비교는 계산에 쓰이지 않으므로 별도 구현하지 않는다 — 대신 Stage 0가 감지 결과를 기존 `inventory` 이벤트로 그대로 내보내므로, 확인 시점에 사람이 "분석" 패널에서 감지된 스택을 보고 직접 판단할 수 있다.
- **Stage 0는 1단계 또는 2단계 중 하나라도 선택했을 때만 실행된다.** 둘 다 안 골랐으면(변경사항 없는 job) Stage 0 없이 곧바로 종료 — 아무것도 안 바꾸는데 버전을 계산/적용할 이유가 없다.
- **1단계·2단계 모두 선택 안 한 경우 예외**: 이 경우엔 Stage 0 자체가 생략되므로 `output_version`도 적용되지 않는다(기존 "변경 사항 없음" 동작 그대로).
- **확인된 버전이 현재 버전과 같으면 거부한다(409).** "동일 버전으로 작업하지 않는다" 정책을 여기서 강제한다.
- **일시정지 지점은 Stage 0 직후, 1단계/2단계 실행 전 딱 한 곳.** 1단계가 선택됐으면 1단계 시작 전에, 2단계만 선택됐으면 2단계 시작 전에 — 결과적으로 "다음 단계 진입 직전" 한 곳으로 통일된다.
- **취약점 스캔은 3곳**: Stage 0(베이스라인, 마이그레이션 전) → 1단계 전체 완료 직후(1단계가 실행됐다면 — 이 결과가 그대로 2단계 패치 대상이 됨, 2단계 자체 스캔은 생략) → 2단계 전체 완료 직후(최종 확인, 화면에도 표시).
- **1단계를 안 돌린 채 2단계만 도는 경우**, 2단계 패치 대상은 Stage 0의 베이스라인 스캔 결과를 재사용한다(다시 스캔하지 않음) — `output_version` 적용은 의존성 버전을 안 건드리므로 Stage 0 스캔 이후 상태가 안 바뀐다. 재사용은 DB에 이미 영구 저장된 `vulnerabilities_baseline` 이벤트를 다시 읽어서 한다(재스캔 대신 재조회 — Trivy/NVD 스캔은 시간이 걸리므로).

## 백엔드 설계

### `models/job.py`

```python
JOB_STATUSES = (
    "queued", "running", "awaiting_version_approval", "awaiting_approval",
    "success", "needs_handoff", "failed", "cancelled",
)
TERMINAL_JOB_STATUSES = frozenset({"success", "needs_handoff", "failed", "cancelled"})
```

`awaiting_version_approval`은 `awaiting_approval`과 같은 성격(터미널 아님, 사람 확인 대기)이다.

### `versioning/artifact_version.py` — 버전 계산

기존 `suggest_output_version`(정규화: `-SNAPSHOT` 제거, `MAJOR.MINOR`→`MAJOR.MINOR.0`)은 그대로 두고, 그 위에 증가 로직을 추가한다.

```python
def compute_stage0_output_version(declared_version: str, run_stage1: bool) -> str:
    """Stage 0's automatic output-version proposal: bump MAJOR if Stage 1
    (a real stack migration) is selected, otherwise bump MINOR -- regardless
    of whether the stack already matches the target (spec: docs/superpowers/
    specs/2026-08-10-stage0-version-scan-restructure-design.md). Falls back
    to the normalized-but-unbumped value when declared_version doesn't parse
    as MAJOR.MINOR.PATCH -- guessing wrong is worse than not guessing."""
    normalized = suggest_output_version(declared_version)
    parts = normalized.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return normalized
    major, minor, _patch = (int(p) for p in parts)
    if run_stage1:
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0"
```

### `api/routers/inspect.py`, `tests/integration/test_inspect_api.py` — 삭제

Part A 전체를 되돌린다. `main.py`의 `inspect_router` import/등록도 제거.

### `orchestration/pipeline.py` — 재구성

`run_pipeline`은 이제 **ingest + Stage 0까지만** 하고 멈춘다(1·2단계 둘 다 안 골랐으면 예외적으로 곧장 종료). 기존에 `run_pipeline`이 하던 "1단계부터 끝까지"는 새 함수 `run_pipeline_resume_after_version_confirm`로 옮긴다 — `run_pipeline_resume_stage2`와 같은 자리에 나란히 둔다.

```python
async def run_pipeline(
    job_id: str, spec: SourceSpec, run_stage1: bool, run_stage2: bool,
    settings: Settings, session_factory: sessionmaker[Session],
) -> None:
    emit, log = make_emit_log(session_factory, job_id)
    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log("POM 분석 시작")
        ingest_result = ingest(job_id, spec, settings)
        work_dir = ingest_result.paths.work
        output_dir = ingest_result.paths.output
        baseline = ingest_result.baseline_commit
        await log(f"모듈 {len(ingest_result.detection.modules)}개, baseline={baseline[:12]}")

        if not (run_stage1 or run_stage2):
            await log("결과물 생성 중...")
            diff_text = diff_since(work_dir, settings, baseline)
            (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
            (output_dir / "report.md").write_text("변경 사항 없음.", encoding="utf-8")
            await set_job_status(session_factory, job_id, "success", report_markdown="변경 사항 없음.")
            await emit("status", {"status": "success"})
            return

        await log("Stage 0: 현재 버전/스택 분석 시작")
        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            work_dir, effective_pom_path, settings,
            log_path=build_log_path(output_dir, "ingest", "mvn-effective-pom"),
        )
        detected = extract_versions(effective_pom_path)
        await emit("inventory", asdict(detected))

        current_version, _source = read_declared_version(effective_pom_path)
        suggested_version = compute_stage0_output_version(current_version, run_stage1) if current_version else None

        await log("마이그레이션 전 취약점 스캔 시작")
        baseline_vulns = await run_combined_scan(work_dir, output_dir, settings)
        await emit("vulnerabilities_baseline", {"vulnerabilities": [asdict(v) for v in baseline_vulns]})
        await log(f"{len(baseline_vulns)}개 취약점 발견 (임계값 이상, 마이그레이션 전)")

        await set_job_status(session_factory, job_id, "awaiting_version_approval")
        await emit("status", {
            "status": "awaiting_version_approval",
            "current_version": current_version,
            "suggested_version": suggested_version,
        })
        return

    except IngestError as exc:
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
```

```python
async def run_pipeline_resume_after_version_confirm(
    job_id: str, confirmed_version: str, settings: Settings, session_factory: sessionmaker[Session],
) -> None:
    """POST /jobs/{id}/confirm-version이 스케줄. work_dir는 Stage 0가 멈춘
    그대로(baseline commit 하나뿐)다."""
    emit, log = make_emit_log(session_factory, job_id)
    with session_factory() as session:
        job = session.get(Job, job_id)
        run_stage1, run_stage2 = job.run_stage1, job.run_stage2

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log(f"출력 아티팩트 버전 설정: {confirmed_version}")
        baseline = await apply_output_version(work_dir, confirmed_version, settings, output_dir=output_dir)
        with session_factory() as session:
            job = session.get(Job, job_id)
            job.output_version = confirmed_version
            session.commit()

        detected = extract_versions(output_dir / "effective-pom.xml")  # Stage 0가 이미 만들어둔 파일 재사용
        report_sections: list[str] = []
        needs_handoff = False
        stage2_vulns: list[Vulnerability] = []

        if run_stage1:
            await log("1단계 스택 마이그레이션 시작")
            stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)
            baseline = current_head(work_dir, settings)
            report_sections.append(stage1_result.report)
            await log(f"1단계 종료: {stage1_result.status}")
            if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
                handoff_dir.mkdir(parents=True, exist_ok=True)
                (handoff_dir / "stage1-guide.md").write_text(stage1_result.handoff_guide, encoding="utf-8")
                needs_handoff = True

            if run_stage2 and not needs_handoff:
                await log("1단계 이후 취약점 재스캔")
                stage2_vulns = await run_combined_scan(work_dir, output_dir, settings)
        elif run_stage2:
            # Stage 0의 베이스라인 스캔을 재사용 -- work/는 그 이후 안 바뀜(버전
            # 적용은 의존성을 안 건드림), 재스캔 대신 저장된 이벤트를 재조회.
            baseline_data = _latest_event_data(session_factory, job_id, "vulnerabilities_baseline")
            stage2_vulns = [Vulnerability(**v) for v in baseline_data["vulnerabilities"]] if baseline_data else []

        awaiting_stage2_approval = run_stage1 and needs_handoff and run_stage2

        if run_stage2 and not awaiting_stage2_approval:
            stage2_report, stage2_needs_handoff = await _run_stage2_block(
                emit, log, job_id, work_dir, output_dir, baseline, handoff_dir, settings, stage2_vulns
            )
            report_sections.append(stage2_report)
            needs_handoff = needs_handoff or stage2_needs_handoff

        if awaiting_stage2_approval:
            diff_text = diff_since(work_dir, settings, ingest_baseline)
            (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
            partial_report = "\n\n---\n\n".join(report_sections)
            (output_dir / "report.md").write_text(partial_report, encoding="utf-8")
            await set_job_status(session_factory, job_id, "awaiting_approval", report_markdown=partial_report)
            await emit("status", {"status": "awaiting_approval"})
            return

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
        final_report = "\n\n---\n\n".join(report_sections) if report_sections else "변경 사항 없음."
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = "needs_handoff" if needs_handoff else "success"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
```

`run_pipeline_resume_stage2`(기존 `awaiting_approval` → `/proceed` 경로)도 `_run_stage2_block`의 새 시그니처(아래)에 맞춰 호출 전에 스캔을 한 번 해서 `vulns`를 넘기도록 고친다 — 이 경로는 1단계가 `needs_handoff`로 끝난 직후이므로 그 시점 상태를 새로 스캔하는 게 맞다(재사용할 이전 스캔이 없음). `try` 블록 맨 앞, 기존 `stage2_report, _stage2_needs_handoff = await _run_stage2_block(...)` 호출 바로 위에 추가:

```python
await log("취약점 재스캔 (2단계 패치 대상 선정)")
vulns = await run_combined_scan(work_dir, output_dir, settings)
stage2_report, _stage2_needs_handoff = await _run_stage2_block(
    emit, log, job_id, work_dir, output_dir, stage_baseline, handoff_dir, settings, vulns
)
```

```python
async def _latest_event_data(session_factory: sessionmaker[Session], job_id: str, event_type: str) -> dict | None:
    with session_factory() as session:
        row = (
            session.query(JobEvent)
            .filter(JobEvent.job_id == job_id, JobEvent.event_type == event_type)
            .order_by(JobEvent.seq.desc())
            .first()
        )
        return row.data if row is not None else None
```

### `orchestration/pipeline.py` — `_run_stage2_block` 시그니처 변경

```python
async def _run_stage2_block(
    emit: EmitFn, log: LogFn, job_id: str, work_dir: Path, output_dir: Path, baseline: str,
    handoff_dir: Path, settings: Settings, vulns: list[Vulnerability],
) -> tuple[str, bool]:
    """더 이상 스스로 스캔하지 않는다 -- 호출자가 이미 스캔한 목록(vulns)을
    받는다(스캔 시점 재배치가 이 함수 밖 책임이 되도록). 패치 후 최종 확인
    스캔은 이 함수가 직접 한다(1단계 needs_handoff 경유든 아니든 공통이라
    여기 두는 게 자연스러움)."""
    await emit("vulnerabilities", {"vulnerabilities": [asdict(v) for v in vulns]})
    await log(f"{len(vulns)}개 취약점 발견 (임계값 이상, 패치 대상)")

    stage2_result = await run_stage2_patches(job_id, work_dir, vulns, baseline, settings, on_log=log)
    success_count = sum(1 for o in stage2_result.outcomes if o.status == "success")
    blocked_count = len(stage2_result.outcomes) - success_count
    await log(f"2단계 종료 (완료 {success_count}건 / 막힘 {blocked_count}건)")

    needs_handoff = False
    for outcome in stage2_result.outcomes:
        if outcome.status == "needs_handoff" and outcome.handoff_guide:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            safe_cve = outcome.vulnerability.cve_id.replace("/", "_")
            (handoff_dir / f"stage2-{safe_cve}-guide.md").write_text(outcome.handoff_guide, encoding="utf-8")
            needs_handoff = True

    await log("2단계 패치 후 최종 취약점 재스캔")
    final_vulns = await run_combined_scan(work_dir, output_dir, settings)
    await emit("vulnerabilities_final", {"vulnerabilities": [asdict(v) for v in final_vulns]})
    await log(f"{len(final_vulns)}개 취약점 남음 (최종)")

    return stage2_result.report, needs_handoff
```

### `api/routers/jobs.py`

- `create_job`: `output_version` Form 파라미터 삭제, `Job(...)` 생성 시 `output_version` 인자 삭제(컬럼은 nullable이라 기본 `None`), `run_pipeline` 호출에서 `output_version` 인자 삭제.
- 새 엔드포인트:

```python
@router.post("/{job_id}/confirm-version", response_model=JobCreateResponse)
async def confirm_version(
    job_id: str, body: ConfirmVersionRequest,
    settings: Settings = Depends(get_settings), db=Depends(get_db_session),
) -> JobCreateResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status != "awaiting_version_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not awaiting version approval (status={job.status})",
        )

    effective_pom_path = settings.jobs_dir / job_id / "output" / "effective-pom.xml"
    current_version, _source = read_declared_version(effective_pom_path)
    if body.output_version == current_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"output version must differ from the current version ({current_version})",
        )

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)
    manager.start(
        job_id, lambda: run_pipeline_resume_after_version_confirm(job_id, body.output_version, settings, factory)
    )
    return JobCreateResponse(job_id=job_id, status="running")
```

- `cancel_job`: `if job.status == "awaiting_approval":` 조건을 `if job.status in ("awaiting_approval", "awaiting_version_approval"):`로 확장(둘 다 "살아있는 Task 없음" 직접 마감 경로).

### `schemas/job.py`

```python
class ConfirmVersionRequest(BaseModel):
    output_version: str
```

## 프론트엔드 설계

### `index.html`

- "출력 아티팩트 버전" `field-row`(수동 입력), `version-hint` `<p>`, `check-version-btn` 버튼 삭제.
- `progress-panel` 안에 버전 확인 UI 추가(아래, job.html과 공유 구조이므로 두 파일에 동일하게):

```html
<div id="version-approval-panel" class="hidden">
  <p class="field-hint">
    감지된 현재 버전: <strong id="detected-current-version"></strong>
    · 제안된 출력 버전: <strong id="suggested-output-version"></strong>
  </p>
  <div class="field-row">
    <label for="confirm-version-input">적용할 출력 버전</label>
    <input id="confirm-version-input" type="text" />
    <button type="button" id="confirm-version-btn">확인하고 계속</button>
  </div>
</div>
```

- `analysis-panel`에 3번째 취약점 표 추가(기존 두 개와 같은 `<details class="vuln-details">` 패턴):

```html
<details id="vuln-final-section" class="vuln-details hidden">
  <summary>오픈소스 취약점 (최종) <span id="vuln-final-count" class="badge">0건</span></summary>
  <div class="table-scroll">
    <table id="vuln-final-table">... 기존과 동일한 컬럼 ...</table>
  </div>
  <p id="vuln-final-empty" class="hidden">임계값 이상 취약점이 발견되지 않았습니다.</p>
</details>
```

### `job.html`

`index.html`과 동일한 두 블록(버전 확인 패널, 3번째 취약점 표) 추가.

### `assets/job-view.js` (index.html/job.html 공유)

- 엘리먼트 참조 추가: `versionApprovalPanel`, `detectedCurrentVersionEl`, `suggestedOutputVersionEl`, `confirmVersionInput`, `confirmVersionBtn`, `vulnFinalSection`, `vulnFinalTableBody`, `vulnFinalEmpty`, `vulnFinalCount`.
- `renderVulnerabilitiesFinal(vulnerabilities)`: 기존 `renderVulnerabilitiesBaseline`/`renderVulnerabilities`와 동일 패턴으로 `renderVulnerabilitiesInto` 재사용.
- SSE 이벤트 리스너 추가: `es.addEventListener("vulnerabilities_final", (ev) => renderVulnerabilitiesFinal(JSON.parse(ev.data).vulnerabilities));`
- `status` 이벤트 핸들러 확장: `data.status === "awaiting_version_approval"`이면 `versionApprovalPanel` 표시, `detectedCurrentVersionEl.textContent = data.current_version ?? "-"`, `suggestedOutputVersionEl.textContent = data.suggested_version ?? "-"`, `confirmVersionInput.value = data.suggested_version ?? ""`. 그 외 상태로 바뀌면 `versionApprovalPanel` 숨김(기존 `proceedBtn` 숨김 처리와 같은 자리).
- `confirmVersionBtn` 클릭 핸들러:

```javascript
confirmVersionBtn.addEventListener("click", async () => {
  const jobId = jobIdDisplay.textContent;
  confirmVersionBtn.disabled = true;
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}/confirm-version`), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ output_version: confirmVersionInput.value.trim() }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    appendLog(`버전 확인 실패: ${err.message}`, true);
    confirmVersionBtn.disabled = false;
  }
});
```

성공하면 버튼을 다시 활성화하지 않는다 — 이미 열려 있는 SSE가 다음 `status`(`running`)를 받으면 `versionApprovalPanel` 자체가 숨겨지므로 별도 처리 불필요(기존 `proceed-btn` 패턴과 동일).

### `assets/app.js`

- `outputVersionInput`, `checkVersionBtn`, `versionHint`, `peekArtifactVersion` 관련 코드 전부 삭제.
- 폼 제출 핸들러에서 `if (outputVersionInput.value.trim()) fd.append("output_version", ...)` 줄 삭제.

## 에러 처리 / 엣지 케이스

- `POST /jobs/{id}/confirm-version`을 `awaiting_version_approval`이 아닌 job에 요청 → 409.
- 확인값이 현재 버전과 동일 → 409, job은 계속 `awaiting_version_approval`로 남아 다시 시도 가능.
- 존재하지 않는 job_id → 404.
- Stage 0에서 `current_version`을 못 찾은 경우(pom.xml에 `<version>`도 `<parent><version>`도 없음, 드묾) → `suggested_version: null`로 그대로 일시정지, 사람이 직접 값을 입력해야 진행 가능(빈 문자열 확인 시도는 프론트에서 막을 필요 없음 — 백엔드가 "현재 버전과 동일" 검사만 하고, `current_version`이 `None`이면 어떤 문자열을 줘도 그 검사에 안 걸리므로 통과됨).
- `awaiting_version_approval` 상태에서 취소(`POST /jobs/{id}/cancel`) → 살아있는 Task 없음, `_finalize_cancelled` 직접 호출(기존 `awaiting_approval`과 동일 경로).
- 1·2단계 둘 다 선택 안 한 job → Stage 0 자체가 없으므로 `awaiting_version_approval`을 거치지 않고 곧바로 `success`.
- 2단계만 선택하고 1단계 `needs_handoff`가 아닌데 2단계로 못 넘어가는 경우는 없음(1단계 자체를 안 돌렸으므로 `needs_handoff`가 될 수 없음) — `stage2_vulns`는 항상 베이스라인 이벤트 재조회로 채워짐.

## 테스트 계획

**단위**:
- `compute_stage0_output_version`: `("1.1.1", True)` → `"2.0.0"`. `("1.0.0", False)` → `"1.1.0"`. `("1.2.3-SNAPSHOT", True)` → `"2.0.0"`(정규화 후 증가). `("1.2.3-RC1", True)` → `"1.2.3-RC1"`(파싱 불가, 그대로).

**통합** (`backend/tests/integration/test_jobs_api.py` 확장 + 필요 시 신규 `test_stage0_api.py`, 기존 `monkeypatch`로 mvn/스캔 mock하는 패턴 재사용):
- 1단계 또는 2단계를 선택한 job 생성 → `awaiting_version_approval`까지 도달, SSE `status` 이벤트에 `current_version`/`suggested_version`이 실리는지.
- `POST /jobs/{id}/confirm-version`에 현재 버전과 같은 값 → 409, 상태는 그대로 `awaiting_version_approval`.
- 다른 값으로 확인 → 200, 최종적으로 `job.output_version`이 그 값으로 저장되고 job이 진행되는지.
- 1·2단계 둘 다 미선택 job → `awaiting_version_approval`을 거치지 않고 바로 `success`.
- `awaiting_version_approval` 상태에서 취소 → `cancelled`로 정상 종료(살아있는 Task 없는 직접 마감 경로).
- 1단계+2단계 모두 선택한 job의 SSE 스트림에서 `vulnerabilities_baseline` → `inventory` → (1단계 로그들) → `vulnerabilities`(1단계 후 스캔 재사용) → (2단계 로그들) → `vulnerabilities_final` 순서로 이벤트가 오는지, 스캔 함수가 정확히 몇 번 호출됐는지(mock call count로 "중복 스캔 없음" 확인).
- 2단계만 선택한 job(1단계 미실행)에서 `vulnerabilities` 이벤트의 내용이 `vulnerabilities_baseline`과 동일한지(재사용 확인), 스캔 함수가 딱 2번만 호출됐는지(베이스라인 + 최종).

**프론트엔드** (수동 스모크, `frontend/README.md` 체크리스트에 추가):
- `index.html`에서 job 시작 후 Stage 0가 끝나면 진행 패널에 감지된 현재 버전/제안된 출력 버전이 표시되고 입력창에 제안값이 미리 채워지는지.
- 현재 버전과 같은 값을 넣고 "확인하고 계속" 클릭 시 에러가 로그에 뜨고 계속 대기 상태인지.
- 다른 값으로 확인 시 패널이 사라지고 1단계(또는 2단계)가 정상적으로 이어지는지.
- 2단계까지 다 끝난 job의 "분석" 패널에 취약점 표 3개(마이그레이션 전/2단계 패치 대상/최종)가 모두 채워지는지.
- `history.html`의 "출력 버전" 열에 확인된 값이 표시되는지.
