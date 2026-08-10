# 구현 계획 — Stage 0 도입 (출력 버전 자동화 + 스캔 재배치)

스펙: [`docs/superpowers/specs/2026-08-10-stage0-version-scan-restructure-design.md`](../specs/2026-08-10-stage0-version-scan-restructure-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아 기존 계획 문서 형식을 따라 직접 작성했다. 스펙에 실제 코드가 거의 그대로 들어 있으므로, 여기서는 반복하지 않고 파일별 적용 순서와 검증만 정리한다. 순서: 모델/순수함수 → 삭제(Part A) → pipeline.py → jobs.py/schemas.py → 프론트엔드 → 전체 검증.

## 0. 사전 확인

- `git status` 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 베이스라인 확인.
- **주의**: 이 계획은 기존 `run_pipeline`의 시그니처와 `POST /jobs`의 `output_version` 파라미터를 없앤다 — 진행 중인 job이 있다면(로컬 개발 중 떠 있는 uvicorn 등) 먼저 종료할 것.

## 1. `models/job.py` — 새 상태 추가

`JOB_STATUSES`에 `"awaiting_version_approval"` 추가(스펙 코드 그대로). `TERMINAL_JOB_STATUSES`는 변경 없음.

**검증**: `grep -rn "awaiting_approval" backend/app`로 이 상수를 쓰는 다른 곳(스트리밍, cancel_job)이 새 상태도 같이 고려해야 하는지 재확인(§4에서 처리).

## 2. `versioning/artifact_version.py` — `compute_stage0_output_version`

스펙 코드 그대로 추가(기존 `suggest_output_version` 아래).

**검증**: `backend/tests/unit/test_artifact_version.py`에 추가:
- `("1.1.1", True)` → `"2.0.0"`.
- `("1.0.0", False)` → `"1.1.0"`.
- `("1.2.3-SNAPSHOT", True)` → `"2.0.0"` (정규화 후 증가).
- `("1.2.3-RC1", True)` → `"1.2.3-RC1"` (파싱 불가, 변화 없음).
- `("1.2", False)` → `"1.3.0"` (정규화로 `1.2.0`이 된 뒤 minor 증가).

## 3. Part A 되돌리기

- `backend/app/api/routers/inspect.py` 삭제.
- `backend/tests/integration/test_inspect_api.py` 삭제.
- `backend/app/main.py`: `inspect_router` import 및 `app.include_router(inspect_router.router)` 삭제.

**검증**: `grep -rn "inspect_router\|routers.inspect" backend/app`로 참조가 안 남았는지 확인. `backend/.venv312/Scripts/python.exe -c "from app.main import create_app; create_app()"`로 앱이 여전히 기동되는지(임포트 에러 없는지) 확인.

## 4. `orchestration/pipeline.py` — 재구성

스펙의 세 함수(`run_pipeline`, `run_pipeline_resume_after_version_confirm`, `_latest_event_data`)와 `_run_stage2_block`의 새 시그니처, `run_pipeline_resume_stage2`의 변경분을 스펙 코드 그대로 적용한다.

- import 추가: `from app.ingest.maven_detect import read_declared_version`, `from app.models.job import Job, JobEvent, TERMINAL_JOB_STATUSES`(`JobEvent` 신규), `from app.scan.merge import Vulnerability`, `from app.versioning.artifact_version import apply_output_version, compute_stage0_output_version`.
- 기존 `run_pipeline`을 스펙의 새 버전으로 완전히 교체(1단계 진입 이전까지만 하고 `awaiting_version_approval`에서 멈춤).
- 새 함수 `run_pipeline_resume_after_version_confirm` 추가(기존 `run_pipeline`의 "1단계부터 끝까지" 로직을 거의 그대로 옮기되, `output_version` 적용과 `stage2_vulns` 소싱 로직 추가).
- `_run_stage2_block` 시그니처에 `vulns: list[Vulnerability]` 파라미터 추가, 내부 스캔 호출 제거, 함수 끝에 최종 스캔 + `vulnerabilities_final` 이벤트 추가.
- `run_pipeline_resume_stage2`: `_run_stage2_block` 호출 전에 스캔해서 `vulns` 만들어 넘기도록 수정.
- `_latest_event_data` 헬퍼 추가.

**검증**: `backend/tests/unit/test_pipeline.py`가 있으면 관련 테스트 확인, 없으면 §5의 통합 테스트로 커버(이 함수들은 실제 mvn/스캔을 mock해야 단위 테스트가 의미 있어 통합 테스트 쪽이 더 적합 — 기존 `test_jobs_api.py`의 `monkeypatch` 패턴 참고). 이 단계에서는 `node --check`에 해당하는 것 없이, 다음 단계(jobs.py)까지 마친 뒤 §7에서 한꺼번에 pytest로 검증한다.

## 5. `api/routers/jobs.py`, `schemas/job.py`

- `schemas/job.py`: `ConfirmVersionRequest(BaseModel)` 추가.
- `jobs.py`:
  - import 추가: `from app.ingest.maven_detect import read_declared_version`, `from app.orchestration.pipeline import run_pipeline_resume_after_version_confirm`(기존 `_finalize_cancelled, run_pipeline, run_pipeline_resume_stage2` 옆에), `from app.schemas.job import ConfirmVersionRequest, JobCreateResponse, JobStatusResponse`.
  - `create_job`: `output_version` Form 파라미터 삭제, `Job(...)` 생성자에서 `output_version=output_version` 인자 삭제, `run_pipeline(job_id, spec, output_version, run_stage1, run_stage2, settings, factory)` 호출을 `run_pipeline(job_id, spec, run_stage1, run_stage2, settings, factory)`로 수정.
  - 새 엔드포인트 `confirm_version` 추가(스펙 코드 그대로, `proceed_job` 아래).
  - `cancel_job`: `if job.status == "awaiting_approval":` → `if job.status in ("awaiting_approval", "awaiting_version_approval"):`.

**검증**: `backend/tests/integration/test_jobs_api.py`에 추가 (기존 `app_client`/`monkeypatch` 패턴 재사용 — `mvn_effective_pom`, `run_combined_scan`, `run_stage1_migration`을 monkeypatch로 가짜 처리해야 실제 mvn/Trivy 없이 빠르게 돈다. `test_cancel_awaiting_approval_job_finalizes_immediately`의 monkeypatch 스타일을 그대로 본뜬다):
- `test_job_reaches_awaiting_version_approval_when_stage_selected`: run_stage1 또는 run_stage2 선택 → SSE `status` 이벤트에 `current_version`/`suggested_version`이 실려 오는지, DB 상태가 `awaiting_version_approval`인지.
- `test_job_skips_stage0_when_no_stage_selected`: 둘 다 미선택 → `awaiting_version_approval`을 거치지 않고 바로 `success`.
- `test_confirm_version_with_same_value_returns_409`.
- `test_confirm_version_with_different_value_proceeds_and_persists_output_version`: 확인 후 job이 진행되고 최종적으로 `job.output_version`이 확인값과 같은지.
- `test_confirm_version_unknown_job_returns_404`.
- `test_confirm_version_when_not_awaiting_returns_409`.
- `test_cancel_awaiting_version_approval_finalizes_immediately`: 기존 `test_cancel_awaiting_approval_job_finalizes_immediately`와 동일 패턴.
- `test_stage2_only_job_reuses_baseline_scan_not_rescan`: 2단계만 선택한 job에서 `run_combined_scan`을 monkeypatch로 call count를 세어, 정확히 2번(베이스라인 + 최종)만 호출되고 재확인 시점엔 재호출 없이 저장된 이벤트를 재사용하는지.
- `test_stage1_and_stage2_job_scans_exactly_three_times`: 둘 다 선택한 job에서 `run_combined_scan` 호출 횟수가 정확히 3번(베이스라인/1단계 후/2단계 후)인지, `vulnerabilities`/`vulnerabilities_final` 이벤트 내용이 각 스캔 결과와 일치하는지.

## 6. 프론트엔드

### `index.html`, `job.html` (동일하게 두 파일 모두)

- `index.html`: "출력 아티팩트 버전" `field-row`, `version-hint`, `check-version-btn` 삭제.
- 두 파일의 `progress-panel` 안에 스펙의 `version-approval-panel` 블록 추가(`stop-btn` 아래, 또는 `log-list` 위 — 배치는 구현 시 자연스러운 자리로).
- 두 파일의 `analysis-panel` 안에 스펙의 `vuln-final-section`(`<details class="vuln-details">`) 블록 추가(기존 `vuln-section` 아래).

### `assets/job-view.js`

- 엘리먼트 참조 추가(스펙 목록 그대로).
- `renderVulnerabilitiesFinal` 함수 추가(`renderVulnerabilitiesInto` 재사용, 기존 `renderVulnerabilitiesBaseline`/`renderVulnerabilities`와 같은 패턴).
- SSE `vulnerabilities_final` 리스너 추가.
- `status` 이벤트 핸들러에 `awaiting_version_approval` 분기 추가(패널 표시/입력값 채움), 그 외 상태에서는 패널 숨김.
- `confirmVersionBtn` 클릭 핸들러 추가(스펙 코드 그대로).

### `assets/app.js`

- `outputVersionInput`, `checkVersionBtn`, `versionHint`, `peekArtifactVersion` 및 그 이벤트 리스너 전부 삭제.
- 폼 제출 핸들러에서 `output_version` FormData append 줄 삭제.

**검증**: `node --check frontend/assets/job-view.js frontend/assets/app.js`.

## 7. `frontend/README.md` — 체크리스트 추가

스펙 §테스트 계획의 "프론트엔드" 항목 추가. 기존에 Part A 관련으로 추가했던 체크리스트 항목(ZIP 자동 조회, "현재 버전 확인" 버튼 관련 3줄)은 기능 자체가 삭제됐으므로 같이 제거한다.

## 8. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인(0단계 베이스라인과 비교).
- 실제 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고, 실제 Maven 프로젝트로:
  1. 1단계만 선택 → Stage 0 완료 후 진행 패널에 현재/제안 버전이 뜨고, 입력창에 제안값이 미리 채워지는지.
  2. 현재 버전과 같은 값 입력 후 확인 → 에러 로그, 계속 대기.
  3. 다른 값으로 확인 → 패널이 사라지고 1단계가 정상 진행, "분석" 패널에 베이스라인 취약점 표가 이미 채워져 있는지.
  4. 1단계 완료 후 취약점 표(2단계 패치 대상)가 자동으로 채워지는지(2단계도 선택했다면), 2단계 완료 후 "최종" 표까지 채워지는지.
  5. `history.html`의 "출력 버전" 열에 확인한 값이 표시되는지.
  6. 1·2단계 둘 다 선택 안 하고 제출 → 버전 확인 단계 없이 바로 성공으로 끝나는지.
  7. `awaiting_version_approval` 상태에서 "중지" 클릭 → 정상적으로 `cancelled`로 끝나는지.

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

- `run_pipeline_resume_after_version_confirm`에서 `apply_output_version` 실패(예외) 시 `except Exception` 블록이 그대로 잡아 `failed`로 끝내는지 확인 — 스펙 코드 상 별도 처리 없이 공용 `except Exception`에 맡기는 것으로 충분한지 구현 시 재확인(기존 `run_pipeline`도 이 함수 실패를 같은 방식으로 처리했었음).
- `version-approval-panel`의 정확한 DOM 삽입 위치(예: `stop-btn` 다음 줄 vs `log-list` 앞)는 스펙에 정해져 있지 않음 — 기존 `field-row` 흐름을 깨지 않는 선에서 구현 시 정한다.
