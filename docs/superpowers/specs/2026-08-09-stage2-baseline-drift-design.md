# Stage 2 롤백 기준점이 낡아서 Stage 1의 성공 커밋을 지우는 문제

## 배경 및 목적

job #14: Stage 1(스택 마이그레이션) 3스텝이 로그상 전부 "완료, 체크포인트 저장"으로 끝났고, `git reflog`로 확인해보니 실제로 5개 커밋(레시피 적용 2회 + 스텝 3개)이 정상적으로 만들어졌었다. 그런데 이후 2단계(CVE 패치)가 진행되던 중 **`git reset --hard`가 1단계 시작 이전 커밋으로 HEAD를 되돌려**, 1단계의 성공 결과물이 통째로 사라졌다(`work/`가 baseline+출력버전 설정 커밋만 남은 상태로 보인 이유).

### 근본 원인

`orchestration/pipeline.py`의 `run_pipeline`은 지역변수 `baseline`을 1단계 실행 **전에** 정하고(`ingest_result.baseline_commit`, 있으면 `apply_output_version`의 반환값), 1단계 실행이 끝난 뒤에는 갱신하지 않는다:

```python
baseline = ingest_result.baseline_commit
if output_version:
    baseline = await apply_output_version(...)
if run_stage1:
    stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)
    # baseline은 여기서 갱신되지 않음 -- 1단계가 만든 체크포인트들을 전혀 반영 못 함
if run_stage2 and not awaiting_stage2_approval:
    stage2_report, ... = await _run_stage2_block(..., baseline, ...)  # 낡은 baseline을 그대로 씀
```

이 낡은 `baseline`은 `stage2_loop.run_stage2_patches`로 전달돼 `last_good_sha`의 **초깃값**이 된다:

```python
last_good_sha = baseline_commit  # 1단계 시작 전의 낡은 값
for vuln in vulnerabilities:
    if 성공: last_good_sha = commit_checkpoint(...)  # 첫 CVE가 성공해야만 갱신됨
    else: reset_to_checkpoint(work_dir, settings, last_good_sha)  # 첫 CVE가 실패하면?
```

2단계가 처리하는 **첫 번째 CVE가 실패**하면 `last_good_sha`가 한 번도 갱신되지 못한 채 `reset_to_checkpoint`가 호출되고, 그 값이 1단계 시작 전 커밋이라 1단계 전체가 롤백된다. job #14에서 정확히 이 순서로 발생했다(`CVE-2026-59901` 패치 실패 → 롤백 → 1단계 5개 커밋 소실).

### 두 번째 인스턴스 (HITL 승인 재개 경로)

`checkpoint/git_repo.resolve_stage_baseline`은 같은 값(`run_pipeline`의 `baseline` 지역변수)을 git 히스토리만으로 재구성하려고 시도하는 함수인데, 독스트링에 그 가정을 그대로 적어놨다: "2번째 커밋(출력 버전 지정 시) 또는 1번째 커밋". 즉 **1단계가 스텝을 하나라도 성공시킨 뒤 막혀서(`needs_handoff`) `awaiting_approval`로 멈추고, 사람이 나중에 승인해 `run_pipeline_resume_stage2`로 2단계를 재개하는 경우**도 똑같이 낡은(1단계 시작 전) 기준점을 쓴다 — 재개된 2단계의 첫 CVE가 실패하면 1단계의 부분 성공분까지 지워질 수 있다. 코드 경로는 다르지만 근본 원인과 고치는 방법은 메인 경로와 동일하다.

## 범위

- 포함: `orchestration/pipeline.py`의 `run_pipeline`(1단계 직후 `baseline` 갱신), `run_pipeline_resume_stage2`(재구성 대신 `current_head` 직접 사용).
- 포함: `checkpoint/git_repo.py`의 `resolve_stage_baseline` 삭제(더 이상 필요 없고, 애초에 잘못된 가정 위에 있던 함수).
- 포함: 관련 테스트 갱신 + job #14를 재현하는 회귀 테스트(메인 경로 + 재개 경로 둘 다).
- 범위 밖: `resolve_ingest_baseline`은 이 버그와 무관(항상 진짜 최초 커밋을 가리키며 여전히 맞음) — 손대지 않는다.
- 범위 밖: `stage2_loop.run_stage2_patches`/`_run_stage2_block`의 함수 시그니처 자체를 바꿔 `baseline`을 내부에서 스스로 계산하게 만드는 방안은 고려했지만 채택하지 않는다 — 지금처럼 호출부가 명시적으로 값을 넘기는 편이 이 코드베이스의 기존 스타일(설정을 암묵적으로 찾지 않고 인자로 명시)과 테스트 용이성에 더 맞는다. 호출부 두 곳만 정확한 값을 넘기도록 고치는 것으로 충분하다.

## 설계

### 1. `orchestration/pipeline.py` — `run_pipeline`

```python
stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)
baseline = current_head(work_dir, settings)  # 신규: 1단계가 실제로 work/에 남긴 상태로 갱신
report_sections.append(stage1_result.report)
```

`no_gap`/`success`/`needs_handoff` 어느 상태로 끝나든 이 시점의 HEAD가 정확한 "보호해야 할 바닥"이므로 조건 분기 없이 항상 갱신한다. `needs_handoff`인 경우 이미 `multi_step.run_stage1_migration`이 실패한 스텝의 미완성 변경만 롤백해뒀으므로(§`docs/architecture.md` §7.3), 그 시점 HEAD는 "마지막으로 검증된 상태"와 정확히 일치한다.

### 2. `orchestration/pipeline.py` — `run_pipeline_resume_stage2`

```python
stage_baseline = current_head(work_dir, settings)  # resolve_stage_baseline(...) 대체
ingest_baseline = resolve_ingest_baseline(work_dir, settings)
```

이 함수를 재개하는 시점엔 `work/`가 1단계가 멈춘 그 상태 그대로이므로(그 사이 아무도 건드리지 않음), 재구성할 필요 없이 현재 HEAD를 그대로 쓰면 된다. 더 이상 쓰지 않는 `output_version = job.output_version` 지역변수도 제거(그 한 곳에만 쓰였다).

### 3. `checkpoint/git_repo.py` — `resolve_stage_baseline` 삭제

더 이상 호출부가 없고, 애초에 "`run_pipeline`의 `baseline` 지역변수를 git 히스토리에서 위치로 재구성한다"는 전제 자체가 틀렸던 함수라 남겨둘 이유가 없다. `resolve_ingest_baseline`은 그대로 둔다(진짜 최초 커밋만 가리키며 이 버그와 무관).

## 테스트 계획

**단위**:
- `tests/unit/test_git_repo.py`: `resolve_stage_baseline` import/사용 제거. 기존 `test_resolve_baselines_without_output_version`/`test_resolve_baselines_with_output_version` 두 테스트를 하나로 정리해 `resolve_ingest_baseline`이 뒤에 커밋(출력 버전 설정 + 1단계 스텝)이 몇 개 더 쌓여도 항상 최초 커밋을 가리키는지만 검증. 파일 상단 독스트링 갱신.
- `tests/unit/test_pipeline.py`: **신규** — job #14 재현. `run_stage1_migration`을 여러 스텝이 성공하는 것처럼 몽키패치(또는 실제로 `commit_checkpoint`를 몇 번 호출하도록 구성)해 1단계가 체크포인트 여러 개를 남기게 한 뒤, `run_stage2_patches`(또는 그 안의 `run_stage2_vulnerability`)를 첫 CVE가 `needs_handoff`로 실패하도록 몽키패치. 실행 후 `git log`로 1단계의 커밋들이 **여전히 남아있는지** 확인(고치기 전이었다면 사라졌을 것). 메인 `run_pipeline` 경로용 하나, `run_pipeline_resume_stage2` 경로용 하나.
- 기존 `test_run_pipeline_resume_stage2_completes_after_approval`은 커밋이 1개뿐인 시나리오라 `current_head`로 바꿔도 동일하게 통과해야 함(그대로 유지, 회귀 확인용으로 재실행만).
