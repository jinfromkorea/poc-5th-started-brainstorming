# Stage 1 인수인계 후 재개 (사람이 직접 고친 `work/`를 이어서 진행)

## 배경 및 목적

job #44에서 Stage 1이 `Spring Boot 3.5 -> 4.0` 스텝에서 `needs_handoff`로 끝났다(원인: `docs/lessons-learned/2026-08-11-jackson3-objectmapper-migration.md` 참고). 사용자가 `backend/data/jobs/44/work/`의 파일을 외부 AI 코딩 도구로 직접 고쳤는데, 이 도구는 `needs_handoff`를 터미널 상태로 취급해서 화면에서 이어서 진행할 방법이 없었다.

이 문서는 `needs_handoff`로 끝난 Stage 1을, 사람이 `work/`를 직접 고친 뒤 화면의 버튼 하나로 검증하고 이어서 진행할 수 있게 하는 기능을 설계한다.

## 범위

- **Stage 1만 대상으로 한다.** `job.status == "stage1_needs_handoff"`인 job에서만 이 기능을 노출/허용한다(`docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md`에서 `needs_handoff`를 `stage1_needs_handoff`/`stage2_needs_handoff`로 분리한 뒤의 상태값). `run_stage2` 값은 따로 확인하지 않는다 — `stage1_needs_handoff`는 오직 "더 이상 자동으로 진행될 게 없을 때"만 붙는 최종 상태라, `run_stage2=true`였던 job이라도 이 상태에 도달했다는 것 자체가 이미 Stage2가 없었거나 끝까지 성공했다는 뜻이다(Stage2가 그 와중에 막혔다면 상태는 `stage2_needs_handoff`가 됐을 것이므로). 그래서 이 기능은 Stage2를 다시 건드릴 필요 없이 항상 "Stage1의 나머지 스텝만 이어서 실행"하면 된다.
- 검증은 **`mvn test-compile` 한 번**만 한다. AI 재시도는 하지 않는다 — 이미 사람이 직접 고친 상태를 확인만 하는 동작이라, 여기서 AI를 다시 태우면 사람의 의도와 다른 방향으로 또 고칠 위험이 있다(실제로 job #44에서 AI가 원래 맞았던 `DateTimeFeature` import를 오히려 틀리게 고친 사례가 있었다 — 레슨런드 문서 참고).
- 범위 밖: Stage 2(개별 CVE) 인수인계 재개, Stage 1+2 동시 선택 job의 재개, 검증 실패 시 AI 자동 재시도.

## 결정 사항

- **게이트**: `job.status == "stage1_needs_handoff"`일 때만 허용. 그 외는 409.
- **새 job 상태를 만들지 않는다.** `stage1_needs_handoff`는 `TERMINAL_JOB_STATUSES`에 그대로 둔다 — "재개 가능한 터미널 상태"라는 개념 자체는 기존에도 없던 게 아니라(사람이 이력에서 삭제할 수도 있어야 함), 이번엔 그 위에 재개 액션 하나를 얹는 것뿐이다.
- **검증 성공 시 재계획은 사내 parent POM 기능과 동일한 메커니즘을 재사용한다**: `mvn effective-pom`을 다시 돌려 현재 `work/`의 실제 스택을 재분석하고, 그 값으로 `build_migration_plan`을 다시 세워 나머지 스텝을 이어서 실행한다. 막혔던 스텝이 정확히 몇 번째였는지는 어디에도 저장하지 않는다 — 재분석된 현재 버전이 이미 레시피가 커밋해둔 버전 변경(예: Boot 4.0.7)을 반영하므로, `build_migration_plan`이 자연스럽게 그 스텝을 계획에서 빼고 다음 스텝부터 잡는다.
- **검증 실패 시**: 아무것도 커밋하지 않고 바로 `stage1_needs_handoff`로 되돌아간다. `output/handoff/stage1-guide.md`를 최신 빌드 출력으로 덮어써서, 사람이 뭐가 아직 안 되는지 바로 볼 수 있게 한다. 재개 버튼은 계속 남아있어 반복 시도 가능하다.
- **리포트는 이어붙인다**: 기존 `job.report_markdown`(1차 시도까지의 내용) 뒤에 이번 재개의 결과를 `\n\n---\n\n`로 이어붙인다(`run_pipeline_resume_stage2`가 이미 쓰는 패턴과 동일).
- **재개가 성공적으로 다 끝나도, 만약 그사이 새로운 스텝에서 또 막히면 다시 `stage1_needs_handoff`(새 가이드로)** — 반복 가능한 사이클이다.
- **재개가 끝까지 성공(`success`)하면, 1차 시도 때 남겨진 `output/handoff/stage1-guide.md`를 지운다.** 안 지우면 job이 성공으로 끝났는데도 결과물 목록에 "아직 인수인계가 필요하다"는 낡은 파일이 남아 헷갈린다(자체 리뷰에서 발견).

## 백엔드 설계

### `orchestration/multi_step.py` — 검증 헬퍼

```python
async def verify_after_manual_fix(work_dir: Path, settings: Settings, on_log: LogFn = noop_log) -> tuple[bool, str]:
    """One-shot mvn test-compile check, no AI retry -- used by Stage 1's
    "인수인계 후 재개" (spec: docs/superpowers/specs/2026-08-11-stage1-
    handoff-resume-design.md). The whole point is confirming a human's own
    fix, not giving the AI another chance to diverge from what the human
    intended."""
    await on_log("인수인계 후 수동 수정 확인 중 (mvn test-compile)")
    result = await mvn_test_compile(work_dir, settings)
    await on_log(f"검증 {'통과' if result.returncode == 0 else '실패'}")
    return result.returncode == 0, result.output
```

(import 추가: `from app.mvnrewrite.mvn_client import mvn_test_compile`)

### `orchestration/pipeline.py` — 새 함수 `run_pipeline_resume_stage1_after_handoff`

`run_pipeline_resume_stage2`와 같은 자리에, 같은 패턴으로 추가한다.

```python
async def run_pipeline_resume_stage1_after_handoff(
    job_id: str, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    """Scheduled by POST /jobs/{id}/resume-stage1 for a job sitting at
    status="stage1_needs_handoff". work_dir is whatever a human left it as
    after manually fixing the code that blocked Stage 1 -- see
    docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md."""
    emit, log = make_emit_log(session_factory, job_id)

    with session_factory() as session:
        job = session.get(Job, job_id)
        prior_report = job.report_markdown or ""

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        ok, build_output = await verify_after_manual_fix(work_dir, settings, on_log=log)

        if not ok:
            guide = build_handoff_guide(
                description="인수인계 후 수동 수정 확인",
                mechanism_used=None,
                messages=[],
                last_build_output=build_output,
                target_summary=TARGET_STACK_SUMMARY,
            )
            handoff_dir.mkdir(parents=True, exist_ok=True)
            (handoff_dir / "stage1-guide.md").write_text(guide, encoding="utf-8")
            await set_job_status(session_factory, job_id, "stage1_needs_handoff")
            await emit("status", {"status": "stage1_needs_handoff"})
            return

        baseline = commit_checkpoint(work_dir, settings, "checkpoint: 인수인계 후 수동 수정 확인됨")

        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            work_dir, effective_pom_path, settings,
            log_path=build_log_path(output_dir, "stage1", "mvn-effective-pom-resume"),
        )
        detected = extract_versions(effective_pom_path)
        await log(
            f"재분석 결과: Java {detected.java_version} / Spring Boot {detected.spring_boot_version} / "
            f"Spring Cloud {detected.spring_cloud_version} / Spring AI {detected.spring_ai_version}"
        )

        stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)

        stage1_guide_path = handoff_dir / "stage1-guide.md"
        if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            stage1_guide_path.write_text(stage1_result.handoff_guide, encoding="utf-8")
        elif stage1_guide_path.exists():
            # The first attempt's guide is now stale -- leaving it would make
            # output/handoff/ claim "still needs a manual fix" for a job that
            # just finished successfully.
            stage1_guide_path.unlink()

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = f"{prior_report}\n\n---\n\n{stage1_result.report}"
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = "stage1_needs_handoff" if stage1_result.status == "needs_handoff" else "success"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- same rationale as run_pipeline
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
```

import 추가: `from app.orchestration.multi_step import TARGET_STACK_SUMMARY, run_stage1_migration, verify_after_manual_fix`, `from app.handoff.guide_builder import build_handoff_guide`.

### `api/routers/jobs.py` — 새 엔드포인트

`proceed_job` 아래에, 같은 패턴으로 추가한다.

```python
@router.post("/{job_id}/resume-stage1", response_model=JobCreateResponse)
async def resume_stage1(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> JobCreateResponse:
    """Resumes Stage 1 after a human manually fixed the code that blocked it
    (spec: docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md).
    Gated purely on status -- see spec's 범위 section for why run_stage2
    doesn't need a separate check here."""
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

import 추가: `run_pipeline_resume_stage1_after_handoff`(기존 `run_pipeline_resume_after_version_confirm, run_pipeline_resume_stage2` 옆에).

## 프론트엔드 설계

`docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md`로 `needs_handoff`가 `stage1_needs_handoff`/`stage2_needs_handoff`로 분리된 덕분에, 버튼 노출 여부를 `status` 문자열 하나만 보고 판단할 수 있다 — `run_stage1`/`run_stage2`를 별도로 threading할 필요가 없다(초안에서는 이게 필요했는데, 상태값 분리로 그 자체가 불필요해졌다).

- `status` 이벤트 핸들러: `data.status === "stage1_needs_handoff"`이면 "인수인계 후 재개" 버튼을 보여주고, 그 외 상태로 바뀌면 숨긴다.
- 버튼 클릭 시 `POST /jobs/{id}/resume-stage1` 호출 → 성공하면 버튼 비활성화(또는 숨김), SSE는 이미 열려 있으면 그대로 이어서 받고, 종료된 job이라 SSE가 닫혀 있었다면 `connectSSE(jobId)`를 다시 호출해 재연결한다(기존 재생 로직이 있어 히스토리부터 다시 보여줌).

### `index.html`, `job.html`

`artifacts-panel` 안, 기존 handoff 가이드 버튼 목록(`handoff-list`) 근처에 조건부(`hidden` 기본) 버튼 추가:

```html
<button type="button" id="resume-stage1-btn" class="hidden">인수인계 후 재개 (수동 수정 확인)</button>
```

## 에러 처리 / 엣지 케이스

- `stage1_needs_handoff`가 아닌 job에 `POST /jobs/{id}/resume-stage1` → 409(`run_stage2=true`였던 job도 별도 체크 불필요 — §범위 참고).
- 존재하지 않는 job_id → 404.
- 검증이 계속 실패하면 몇 번이든 반복 클릭 가능 — 매번 `stage1-guide.md`를 최신 실패로 덮어씀.
- 재개 도중 취소(`POST /jobs/{id}/cancel`)는 이번 범위에 포함한다 — `run_pipeline_resume_stage1_after_handoff`도 다른 resume 함수들과 동일하게 `except asyncio.CancelledError`로 `_finalize_cancelled`를 부르므로 별도 구현 없이 이미 동작한다(`status=running`인 동안은 실행 중인 Task가 있으므로 기존 `cancel_job`의 일반 경로로 처리됨).
- 재개가 성공해서 나머지 계획까지 전부 끝났는데 그 중간에 또 다른 스텝이 막히면 → 다시 `stage1_needs_handoff`, 재개 버튼 그대로 다시 노출.

## 테스트 계획

**단위** (`tests/unit/test_multi_step.py`에 추가):
- `verify_after_manual_fix`: `mvn_test_compile` mock 성공/실패에 따라 `(True/False, output)` 반환하는지.

**통합** (`tests/integration/test_jobs_api.py`에 추가, 기존 monkeypatch 패턴 재사용):
- `test_resume_stage1_rejected_when_not_stage1_needs_handoff` — 아무 상태에서나 시도 시 409.
- `test_resume_stage1_allowed_when_run_stage2_true` — `run_stage1=true, run_stage2=true`였던 job이 `stage1_needs_handoff`(= Stage2까지 이미 끝난 뒤 Stage1 문제만 남은 상태)로 끝나 있으면 정상적으로 재개되는지 — §범위의 핵심 근거를 실제로 검증하는 회귀 테스트.
- `test_resume_stage1_verify_fails_stays_needs_handoff` — `mvn_test_compile` 실패 mock → 상태 그대로 `stage1_needs_handoff`, `mvn_effective_pom`/`run_stage1_migration`은 호출 안 됨(재분석/재계획 자체를 안 탐).
- `test_resume_stage1_verify_succeeds_completes_migration` — `mvn_test_compile`/`mvn_effective_pom`/`run_stage1_migration`(나머지 계획 없음, 바로 성공) mock → 최종 `success`, `report_markdown`에 이전 리포트와 새 리포트가 모두 포함되는지, 1차 시도 때 만들어진 `output/handoff/stage1-guide.md`가 삭제되는지.
- `test_resume_stage1_verify_succeeds_but_next_step_blocks` — 재개 후 다음 스텝에서 또 막히는 경우 → 다시 `stage1_needs_handoff`, 새 handoff 가이드 파일 내용이 갱신되는지.
