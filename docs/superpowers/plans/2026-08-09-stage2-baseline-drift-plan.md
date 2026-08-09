# 구현 계획 — Stage 2 롤백 기준점 낡음 버그 수정

스펙: [`docs/superpowers/specs/2026-08-09-stage2-baseline-drift-design.md`](../specs/2026-08-09-stage2-baseline-drift-design.md)

`writing-plans` 스킬이 이 환경에 없어(반복 확인됨) 스펙을 바탕으로 직접 작성했다. 단계 순서: pipeline.py 두 곳 수정 → git_repo.py 정리 → 기존 테스트 정리 → 신규 회귀 테스트(메인/재개 경로) → 전체 검증.

## 0. 사전 확인

- `git status` 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` 베이스라인 통과 확인.

## 1. `orchestration/pipeline.py` — import 정리 + `run_pipeline` 수정

- import 줄을 `from app.checkpoint.git_repo import current_head, diff_since, resolve_ingest_baseline`로 변경(`resolve_stage_baseline` 제거, `current_head` 추가).
- `stage1_result = await run_stage1_migration(...)` 바로 다음 줄에 `baseline = current_head(work_dir, settings)` 추가(스펙 §설계 1).

**검증**: 4단계 테스트로.

## 2. `orchestration/pipeline.py` — `run_pipeline_resume_stage2` 수정

- `stage_baseline = resolve_stage_baseline(work_dir, settings, output_version)` → `stage_baseline = current_head(work_dir, settings)`.
- 이제 안 쓰는 `output_version = job.output_version` 줄 제거(`with session_factory() as session:` 블록 안에서 `job.output_version`을 더 이상 안 읽음 — `prior_report = job.report_markdown or ""`만 남음).
- 함수 독스트링의 "resolve_ingest_baseline/resolve_stage_baseline" 언급도 "resolve_ingest_baseline/current_head"로 갱신.

**검증**: 4단계 테스트로.

## 3. `checkpoint/git_repo.py` — `resolve_stage_baseline` 삭제

- 함수 전체 삭제. `resolve_ingest_baseline`은 그대로.

**검증**: `grep -rn "resolve_stage_baseline" backend/app backend/tests`로 참조가 하나도 안 남았는지 확인(3단계 후 테스트 파일에서도 제거 완료 상태여야 함 — 순서상 이 grep은 5단계 이후에 최종 확인).

## 4. `tests/unit/test_git_repo.py` 정리

- import에서 `resolve_stage_baseline` 제거.
- `test_resolve_baselines_without_output_version`/`test_resolve_baselines_with_output_version` 두 테스트를 다음 하나로 합침(이름 예: `test_resolve_ingest_baseline_ignores_later_commits`):
  - ingest baseline 커밋 → 출력 버전 설정 커밋 → 1단계 스텝 체크포인트 커밋까지 3개를 쌓고, `resolve_ingest_baseline(work_dir, settings)`가 항상 맨 처음 커밋을 가리키는지, `current_head(work_dir, settings)`는 그 셋과 다 다른지(가장 최신 커밋)를 확인.
- 파일 상단 독스트링에서 `resolve_stage_baseline` 언급 제거.

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/unit/test_git_repo.py -q --basetemp=/c/pytesttmp`

## 5. `tests/unit/test_pipeline.py` — job #14 재현 회귀 테스트 신규 추가

기존 파일의 몽키패치 패턴(`app.orchestration.pipeline.run_stage1_migration`, `app.orchestration.pipeline.run_stage2_patches` 등)을 그대로 따른다. `run_combined_scan`/`mvn_effective_pom`/`extract_versions`도 기존 테스트들처럼 스텁 처리.

### 5a. 메인 경로: `test_stage1_success_commits_survive_a_failed_first_stage2_cve`

- `job_paths.work`에 `git_init_and_baseline_commit` 후, 가짜 `run_stage1_migration`이 실행되는 **동안 실제로 `commit_checkpoint`를 2~3번 호출**해 진짜 커밋 여러 개를 남기게 하고(단순히 `MigrationRunResult(status="success", ...)`만 반환하는 게 아니라, 실제 git 상태가 있어야 버그가 재현됨), `status="success"`를 반환하도록 구성.
- 가짜 `run_stage2_patches`는 `Stage2RunResult`를 만들기 전에 **`reset_to_checkpoint(work_dir, settings, baseline_commit)`을 호출**하도록 구성 — 이게 바로 `stage2_loop.py`의 "첫 CVE 실패" 시나리오가 실제로 하는 일이므로, 여기서 CVE 시뮬레이션까지 다 짤 필요 없이 이 한 줄로 재현 가능. 넘어온 `baseline_commit` 인자가 고친 코드에서는 1단계 이후 커밋(정확한 값), 안 고쳤다면 1단계 이전 커밋(낡은 값)이 될 것.
- `run_pipeline(...)` 실행 후 `git_repo.current_head`로 work_dir의 HEAD를 확인하거나 `git log`를 직접 파싱해서, 1단계 체크포인트 커밋들이 **여전히 조상 커밋으로 남아있는지**(`git merge-base --is-ancestor` 또는 로그에 커밋 메시지가 있는지) 확인.

### 5b. 재개 경로: `test_resume_stage2_after_partial_stage1_success_preserves_stage1_commits`

- `job_paths.work`에 `git_init_and_baseline_commit` + **실제 `commit_checkpoint`를 2번 호출**(1단계가 스텝 2개는 성공하고 3번째에서 막힌 상황을 흉내) → `awaiting_approval` 상태의 `Job` row 생성(기존 `test_run_pipeline_resume_stage2_completes_after_approval`과 같은 패턴).
- 가짜 `run_stage2_patches`가 (5a와 동일하게) 넘어온 `baseline_commit`으로 `reset_to_checkpoint`를 호출하도록 구성.
- `run_pipeline_resume_stage2(...)` 실행 후 그 2개의 1단계 체크포인트 커밋이 살아남는지 확인.

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/unit/test_pipeline.py -q --basetemp=/c/pytesttmp` — 새 테스트 2개가 (수정 전 코드에 대고 돌리면 실패하고) 수정 후 코드에서는 통과하는지 감으로도 한 번 확인.

## 6. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` — 0단계 베이스라인 대비 새로 깨진 테스트 없는지, 전체 통과.
- `grep -rn "resolve_stage_baseline" backend/app backend/tests` — 결과 없어야 함(3단계 참고).
- (선택) job #14와 같은 실제 시나리오(ace-parent, 출력버전 1.0.0, 1·2단계 둘 다 실행)를 실제로 재현해서 `work/`의 `git log`에 1단계 커밋들이 2단계 이후에도 살아있는지 최종 확인 — job cancellation 기능 스모크 테스트 때처럼 실제 서버 띄워서 확인 가능하지만, 실제 LLM 호출까지 필요해 비용이 든다.
