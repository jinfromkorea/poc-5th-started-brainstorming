# 구현 계획 — 이력 삭제 + 분석 영역 접기/펼치기

스펙: [`docs/superpowers/specs/2026-08-10-history-delete-and-analysis-collapse-design.md`](../specs/2026-08-10-history-delete-and-analysis-collapse-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아(`skills-lock.json`에 `brainstorming`만 등록됨) 이 문서는 기존 `2026-08-08-job-cancellation-plan.md`의 형식을 그대로 따라 직접 작성했다. 단계는 의존성 순서(백엔드 모델 → 백엔드 API → 프론트엔드 이력 화면 → 프론트엔드 상세 화면 → 테스트)를 따른다. 각 단계 뒤에 "검증"을 명시했으니, 구현 중 막히면 이전 단계로 돌아가지 말고 해당 단계의 검증부터 다시 확인한다.

## 0. 사전 확인

- 현재 `git status`가 깨끗한지 확인하고 시작한다(이 계획과 무관한 미커밋 변경과 섞이지 않도록).
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 기존 테스트가 전부 통과하는 베이스라인을 확인한다.

## 1. `models/job.py` — 채번 방식 변경

- `next_job_id()`를 `COUNT(*)+1`에서 `MAX(CAST(id AS INTEGER))+1`로 변경:

```python
from sqlalchemy import JSON, DateTime, Integer, String, cast, func
...

def next_job_id(db: Session) -> str:
    """지금까지 나온 가장 큰 id + 1. job이 삭제될 수 있으므로 COUNT(*) 기반
    채번은 ID 충돌을 일으킬 수 있다 (예: job 1/2/3 중 2를 삭제하면 count=2 ->
    다음 id "3"이 기존 job 3과 PK 충돌). MAX 기반은 삭제 후에도 항상 안전하게
    증가하며, 삭제된 id는 재사용되지 않는다. cast(Job.id, Integer)는
    SQLite 기준으로만 검증한다 -- 이 프로젝트는 SQLite 외 DB를 쓸 계획이
    없다(DATABASE_URL=sqlite:///...)."""
    max_id = db.query(func.max(cast(Job.id, Integer))).scalar()
    return str((max_id or 0) + 1)
```

- 기존 docstring/주석 중 "jobs are never deleted (there's no delete endpoint)" 전제를 언급하는 부분을 삭제 기능이 생겼다는 사실에 맞게 수정.

**검증**: `grep -rn "next_job_id" backend` 로 호출부가 `create_job` 한 곳뿐인지 재확인. 신규 단위 테스트(`backend/tests/unit/`에 `test_job.py`가 없으면 새로 생성, 있으면 추가) — **SQLite 기준으로만 작성**(다른 DB 방언 호환성은 검증 대상이 아님, 프로젝트가 SQLite만 지원): job 1/2/3을 만들고 2를 지운 뒤 `next_job_id()`가 "4"를 반환하는지(직접 `Job` row를 add/delete해서 재현, 파이프라인 실행 불필요). 이 테스트가 실제 `cast(Job.id, Integer)` -> `CAST(jobs.id AS INTEGER)` 번역이 SQLite에서 의도대로 동작함을 확인하는 유일한 지점이다.

## 2. `api/routers/jobs.py` — `DELETE /jobs/{job_id}`

- `from app.models.job import TERMINAL_JOB_STATUSES, Job, JobEvent, next_job_id`로 `JobEvent` import 추가.
- `cancel_job` 아래, `job_events` 라우트 위에 추가:

```python
@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not terminal (status={job.status}); cancel it first",
        )

    # job_events에는 jobs로의 FK/relationship이 없어(job_id는 논리적 참조일
    # 뿐) ORM cascade가 걸리지 않는다 -- db.delete(job) 전에 명시적으로
    # 지운다.
    db.query(JobEvent).filter(JobEvent.job_id == job_id).delete()
    db.delete(job)
    db.commit()

    job_dir = settings.jobs_dir / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)
```

`shutil`은 이미 파일 상단에 import돼 있음(zip 업로드 처리에 사용 중).

**구현 중 발견한 이슈**: Windows에서는 `shutil.rmtree`가 `work/.git/objects/**`의 읽기 전용 파일에서 `PermissionError: [WinError 5]`로 실패한다(git이 커밋된 객체 파일을 읽기 전용으로 만들기 때문). `onexc` 콜백으로 읽기 전용 플래그를 해제하고 재시도하도록 처리:

```python
def _rmtree_clear_readonly(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)

...
shutil.rmtree(job_dir, onexc=_rmtree_clear_readonly)
```

`os`, `stat` import 추가 필요.

**검증**: `backend/tests/integration/test_jobs_api.py`에 추가 (기존 `test_cancel_*` 테스트들의 fixture/스타일 그대로 재사용):
- `test_delete_terminal_job_removes_row_and_directory`: `app_client`로 job을 하나 완주시켜 terminal 상태로 만든 뒤(`_wait_for_terminal_status` 재사용) `DELETE /jobs/{id}` → 204, 이후 `GET /jobs/{id}` → 404, `backend/data/jobs/{id}/`(테스트에서는 `tmp_path` 하위 경로) 디렉터리가 사라졌는지.
- `test_delete_non_terminal_job_returns_409`: `queued`/`running` 상태에서 삭제 시도 → 409 (기존 `test_cancel_already_terminal_job_returns_409`의 반대 케이스, 같은 방식으로 상태를 세팅).
- `test_delete_unknown_job_returns_404`.
- `test_delete_then_create_reuses_next_available_id`: 위 §1 단위 테스트와 별개로, API 레벨에서 삭제 후 새 job 생성 시 id가 충돌 없이 이어지는지 한 번 더 확인(엔드투엔드 회귀 방지).

## 3. `history.html` — 작업 열 추가

- `<thead>`의 마지막 `<th>수정 시각</th>` 뒤에 `<th>작업</th>` 추가.

**검증**: 브라우저에서 `history.html`을 열어 표 헤더에 "작업" 열이 보이는지만 육안 확인(내용은 다음 단계에서 채움).

## 4. `assets/history.js` — 행별 중지/삭제 버튼 + 핸들러

- 상단에 `const ACTIVE_STATUSES = new Set(["queued", "running", "awaiting_approval"]);` 추가.
- `loadJobs()`의 행 생성 루프 끝(`jobsTableBody.appendChild(row)` 직전)에 액션 셀 추가:

```javascript
const actionCell = document.createElement("td");
const actionBtn = document.createElement("button");
actionBtn.type = "button";
actionBtn.className = "secondary";
if (ACTIVE_STATUSES.has(job.status)) {
  actionBtn.textContent = "중지";
  actionBtn.addEventListener("click", () => stopJob(job.job_id, actionBtn));
} else {
  actionBtn.textContent = "삭제";
  actionBtn.addEventListener("click", () => deleteJob(job.job_id, actionBtn));
}
actionCell.appendChild(actionBtn);
row.appendChild(actionCell);
```

- `stopJob`/`deleteJob` 함수 추가 (스펙 §프론트엔드 설계의 코드 그대로):

```javascript
async function stopJob(jobId, btn) {
  if (!confirm("정말 이 작업을 중지할까요? 지금까지의 변경 내용은 저장되지 않습니다.")) return;
  btn.disabled = true;
  btn.textContent = "중지 중...";
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}/cancel`), { method: "POST", headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`중지 요청 실패: ${err.message}`);
  } finally {
    loadJobs();
  }
}

async function deleteJob(jobId, btn) {
  if (!confirm("이 작업 이력을 삭제할까요? 관련 파일도 함께 삭제되며 복구할 수 없습니다.")) return;
  btn.disabled = true;
  btn.textContent = "삭제 중...";
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}`), { method: "DELETE", headers: authHeaders() });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  } finally {
    loadJobs();
  }
}
```

**검증**: `node --check frontend/assets/history.js`로 문법 오류만 우선 확인(이 프로젝트에 프론트 자동 테스트가 없음 — job-cancellation-plan과 동일 방침). 실사용 검증은 §6 전체 검증에서 수행.

## 5. `job.html` — 취약점 섹션을 `<details>`로 전환

- `<div id="vuln-baseline-section" class="hidden">` → `<details id="vuln-baseline-section" class="vuln-details hidden">`(닫는 태그도 `</details>`로), `<h3>` 제목 뒤에 카운트 배지 추가:

```html
<details id="vuln-baseline-section" class="vuln-details hidden">
  <summary>오픈소스 취약점 (마이그레이션 전) <span id="vuln-baseline-count" class="badge">0건</span></summary>
  <div class="table-scroll">
    ... 기존 table 그대로 ...
  </div>
  <p id="vuln-baseline-empty" class="hidden">임계값 이상 취약점이 발견되지 않았습니다.</p>
</details>
```

- `vuln-section`도 동일하게 `<details>` + `<summary>` + `id="vuln-count"` 배지로 변경.
- 두 `<details>` 모두 `open` 속성을 주지 않음(기본 접힘).
- `<h3>` 태그는 `<summary>` 안으로 흡수되므로 별도 `<h3>` 요소는 제거.

**검증**: 브라우저에서 `job.html` 정적 마크업만으로 접기/펼치기가 동작하는지(내용 없이도 `<details>` 자체는 JS 없이 네이티브로 동작) 육안 확인.

## 6. `assets/job-view.js` — 카운트 배지 갱신

- 상단 참조에 `const vulnBaselineCount = el("vuln-baseline-count");`, `const vulnCount = el("vuln-count");` 추가.
- `renderVulnerabilitiesInto`의 옵션 객체에 `countBadge` 추가하고 함수 본문 맨 앞(또는 `section.classList.remove("hidden")` 다음 줄)에 `countBadge.textContent = \`${vulnerabilities.length}건\`;` 추가.
- `renderVulnerabilitiesBaseline`/`renderVulnerabilities` 호출부에 각각 `countBadge: vulnBaselineCount` / `countBadge: vulnCount` 추가.

**검증**: `node --check frontend/assets/job-view.js`. 실사용 검증은 §8에서 실제 취약점이 있는 job으로 확인.

## 7. `assets/app.css` — `<details>/<summary>` 최소 스타일

- 기존 `.table-scroll`/`.badge` 규칙 근처에 추가:

```css
.vuln-details > summary {
  cursor: pointer;
  font-weight: 600;
  margin: 1rem 0 0.5rem;
}
```

**검증**: 브라우저에서 제목에 마우스 올렸을 때 커서가 포인터로 바뀌는지 육안 확인.

## 8. `frontend/README.md` — 수동 스모크 체크리스트 추가

스펙 §테스트 계획의 "프론트엔드" 목록을 기존 체크리스트 형식(`- [ ] ...`)에 맞춰 그대로 추가.

## 9. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인(0단계 베이스라인과 비교해 새로 깨진 테스트가 없는지).
- 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고:
  1. `history.html`에서 이미 끝난(success/failed/cancelled) job 행에 "삭제" 버튼이 보이고, 클릭 → 확인 다이얼로그 → 목록에서 사라지고 `backend/data/jobs/{id}/` 디렉터리가 실제로 지워졌는지.
  2. 진행 중인 job을 하나 시작해 `history.html`에서 "중지" 버튼이 보이는지, 클릭 후 상태가 `cancelled`로 바뀌면 "삭제" 버튼으로 자동 전환되는지.
  3. 취약점이 있는 job의 `job.html`에서 두 표가 기본 접힌 채로 로드되고, 배지 건수가 표 실제 행 수와 일치하며, 클릭 시 펼쳐지는지.
  4. 삭제 후 새 job을 하나 시작해 id가 삭제된 번호와 충돌하지 않고 이어지는지.
