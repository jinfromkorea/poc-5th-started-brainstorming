# 이력 삭제 + 분석 영역 접기/펼치기

## 배경 및 목적

현재 job은 삭제할 방법이 없다 (append-only 설계, `next_job_id()`는 이를 전제로 `COUNT(*)+1`로 채번한다). job이 쌓일수록 `history.html`의 "작업 목록"이 길어지고, `backend/data/jobs/` 디스크 사용량도 늘어난다 (현재 19개 job에 약 335MB — 대부분 `work/` 디렉터리의 전체 git 리포지토리 복사본과 `target/` 빌드 산출물). 더 이상 필요 없는 이력을 사용자가 직접 지워 목록과 디스크를 정리할 수 있어야 한다.

또한 `job.html`의 "분석" 영역에서 두 취약점 표(마이그레이션 전 / 2단계 패치 대상)가 항상 펼쳐진 채로 나오는데, 취약점이 많은 job에서는 화면을 많이 차지한다. 몇 건인지 한눈에 보이지 않는 것도 불편하다.

## 범위

- 백엔드: `DELETE /jobs/{job_id}` 엔드포인트, job id 채번 방식 수정
- 프론트엔드 (`history.html`): "작업 목록" 표에 행별 "중지"/"삭제" 버튼 추가
- 프론트엔드 (`job.html`): 취약점 두 표를 `<details>`로 감싸 접기/펼치기 + 건수 배지

범위 밖: 일괄(전체) 삭제 버튼, job 삭제의 되돌리기(휴지통/soft delete), `history.html`의 페이지네이션/필터링(기존과 동일하게 미지원 유지).

## 결정 사항

- **삭제 방식은 하드 삭제**: DB 행(`jobs`, `job_events`)과 `backend/data/jobs/{id}/` 디렉터리 전체(`source/`, `work/`, `output/`)를 즉시 제거한다. 로컬 1인 개발자 도구이고 디스크 회수가 목적이므로 soft delete(플래그만 세우고 파일은 남김)는 채택하지 않는다. 되돌릴 수 없으므로 프론트엔드에서 반드시 확인(confirm) 다이얼로그를 거친다.
- **삭제 가능 상태 = 종료 상태**: `success`/`needs_handoff`/`failed`/`cancelled`(`TERMINAL_JOB_STATUSES`)만 삭제 가능. `queued`/`running`/`awaiting_approval`은 기존 `POST /jobs/{id}/cancel`이 취소 가능하다고 보는 상태와 동일하게 취급해, 먼저 중지해야 삭제할 수 있다 — 실행 중인 파이프라인이 쓰고 있는 디렉터리를 지우면 오류/고아 파일이 생길 수 있기 때문이다.
- **행별 버튼, 전체 삭제 버튼 없음**: `history.html` 표의 각 행에 상태에 따라 "중지" 또는 "삭제" 버튼 하나만 노출한다. 일괄 삭제는 YAGNI로 범위 밖.
- **채번 방식을 `MAX+1` 기반으로 변경**: 기존 `next_job_id()`는 `COUNT(*)+1`이라 삭제가 생기면 ID 충돌을 일으킨다 (예: job 1/2/3 중 2를 삭제하면 count=2 → 다음 신규 job이 다시 "3"이 되어 기존 job 3과 PK 충돌). `SELECT MAX(CAST(id AS INTEGER))` 방식으로 바꿔 이 문제를 근본적으로 막는다. 삭제된 ID는 재사용되지 않는다(항상 지금까지 나온 가장 큰 번호+1).
- **취약점 표는 네이티브 `<details>`/`<summary>` 사용**: 프로젝트에 아직 아코디언 패턴이 없다 (`hidden` 클래스 토글만 사용 중). 커스텀 JS 토글보다 `<details>`가 더 단순하고 접근성도 기본 제공되므로 이걸 첫 도입 패턴으로 채택한다. 기본 상태는 **접힘**.

## 백엔드 설계

### `models/job.py` — `next_job_id`

```python
def next_job_id(db: Session) -> str:
    """job이 삭제될 수 있으므로 COUNT(*) 기반 채번은 ID 충돌을 일으킬 수 있다
    (예: 2번 삭제 후 count=2 -> 다음 id "3"이 기존 job 3과 충돌). 지금까지
    나온 가장 큰 id + 1을 쓰면 삭제 후에도 항상 안전하게 증가한다."""
    max_id = db.query(func.max(cast(Job.id, Integer))).scalar()
    return str((max_id or 0) + 1)
```

기존 docstring의 "jobs are never deleted" 전제가 깨지므로 주석도 함께 수정한다.

### `api/routers/jobs.py` — `DELETE /jobs/{job_id}`

```python
@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, settings: Settings = Depends(get_settings), db=Depends(get_db_session)) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not terminal (status={job.status}); cancel it first",
        )

    db.query(JobEvent).filter(JobEvent.job_id == job_id).delete()
    db.delete(job)
    db.commit()

    job_dir = settings.jobs_dir / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)
```

`job_events`를 먼저 지우고 `jobs` 행을 지운 뒤 커밋하고, 그다음에 디스크 삭제를 한다 — DB 커밋이 먼저 성공해야 목록에서 즉시 사라지고, 디스크 삭제가 혹시 실패해도(권한 등) DB 상태와 API 응답은 이미 일관되게 끝난 뒤이므로 사용자에게 혼란을 주지 않는다. 라우트 순서는 기존 `GET /jobs` / `GET /jobs/{job_id}` 아래, 다른 `/{job_id}/...` 라우트들과 같은 블록에 추가한다.

## 프론트엔드 설계

### `history.html` / `assets/history.js` — 행별 중지/삭제 버튼

표 헤더에 "작업" 열 추가:

```html
<th>ID</th><th>상태</th><th>소스</th><th>출력 버전</th>
<th>1단계</th><th>2단계</th><th>생성 시각</th><th>수정 시각</th><th>작업</th>
```

`loadJobs()`의 행 생성 루프 끝에 상태에 따라 버튼 하나를 붙인다:

```javascript
const ACTIVE_STATUSES = new Set(["queued", "running", "awaiting_approval"]);

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

`stopJob`은 `job-view.js`의 기존 중지 버튼 핸들러(확인 다이얼로그 → `POST /jobs/{id}/cancel`)와 같은 패턴을 따르되, SSE 연결이 없는 이 페이지에서는 상태 반영을 SSE로 받을 수 없으므로 호출 성공 후 `loadJobs()`를 다시 불러 표를 갱신한다:

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

중지 요청 직후에는 백엔드가 아직 실제 취소를 마무리하기 전일 수 있어(§job-cancellation-design.md) `loadJobs()`로 다시 불러도 상태가 곧바로 `cancelled`가 아닐 수 있다 — "새로고침" 버튼으로 다시 확인하면 된다는 기존 관용(자동 폴링 없음)을 그대로 따른다.

### `job.html` / `assets/job-view.js` / `assets/app.css` — 취약점 표 접기/펼치기 + 건수

`job.html`의 두 `<div id="vuln-baseline-section">`/`<div id="vuln-section">`를 `<details>`로 교체하고 제목 옆에 건수 배지를 추가:

```html
<details id="vuln-baseline-section" class="vuln-details hidden">
  <summary>오픈소스 취약점 (마이그레이션 전) <span id="vuln-baseline-count" class="badge">0건</span></summary>
  <div class="table-scroll"> ... 기존 table 동일 ... </div>
  <p id="vuln-baseline-empty" class="hidden">임계값 이상 취약점이 발견되지 않았습니다.</p>
</details>
```

`vuln-section`도 동일하게 `id="vuln-count"` 배지로 바꾼다. 최상위 `hidden` 클래스는 데이터가 도착하기 전 페이지 전체를 숨기는 기존 용도를 그대로 유지하고(`renderVulnerabilitiesInto`가 지금처럼 `classList.remove("hidden")`), 접힘/펼침은 `<details>` 자체의 `open` 속성이 담당하므로 서로 간섭하지 않는다. `open` 속성을 주지 않아 기본은 접힘.

`job-view.js`의 `renderVulnerabilitiesInto`에 카운트 배지 갱신 한 줄만 추가:

```javascript
function renderVulnerabilitiesInto(vulnerabilities, { section, tableBody, emptyMsg, countBadge }) {
  analysisPanel.classList.remove("hidden");
  section.classList.remove("hidden");
  countBadge.textContent = `${vulnerabilities.length}건`;
  ...
}

function renderVulnerabilitiesBaseline(vulnerabilities) {
  renderVulnerabilitiesInto(vulnerabilities, {
    section: vulnBaselineSection,
    tableBody: vulnBaselineTableBody,
    emptyMsg: vulnBaselineEmpty,
    countBadge: vulnBaselineCount,
  });
}
// renderVulnerabilities도 동일하게 countBadge: vulnCount 추가
```

`app.css`에 `<details>/<summary>` 최소 스타일만 추가 (제목 클릭 가능하게 커서 포인터, 기존 `h3`과 비슷한 폰트 — 커스텀 화살표 아이콘 없이 브라우저 기본 마커 사용):

```css
.vuln-details > summary {
  cursor: pointer;
  font-weight: 600;
  margin: 1rem 0 0.5rem;
}
```

## 에러 처리 / 엣지 케이스

- 존재하지 않는 job_id 삭제 요청 → 404.
- 진행중 상태(`queued`/`running`/`awaiting_approval`) 삭제 요청 → 409 ("먼저 중지하세요").
- 디스크에 job 디렉터리가 이미 없는 상태(예: 수동으로 미리 지움)에서 삭제 요청 → DB 행은 정상 삭제, `job_dir.exists()` 체크로 `shutil.rmtree` 생략(에러 아님).
- 중지 버튼 클릭 후 실제로는 이미 종료된 job(경쟁 상태: 다른 탭에서 먼저 끝남) → 기존 cancel 엔드포인트가 409 반환, `alert`로 표시 후 `loadJobs()`가 최신 상태 반영.
- 취약점 0건인 표 → `<details>`는 그대로 두되(접었다 펼치면 "발견되지 않았습니다" 문구), 배지는 "0건".

## 테스트 계획

**통합** (`backend/tests/integration/test_jobs_api.py`에 추가):
- 종료 상태(`success`/`failed`/`cancelled` 등) job에 `DELETE /jobs/{id}` → 204, 이후 `GET /jobs/{id}` → 404, `backend/data/jobs/{id}/` 디렉터리가 사라졌는지.
- `queued`/`running`/`awaiting_approval` 상태에서 `DELETE /jobs/{id}` → 409.
- 존재하지 않는 job_id → 404.
- 삭제 후 새 job 생성 시 id가 삭제된 id를 재사용하지 않고 기존 최대값+1로 이어지는지 (`next_job_id` 회귀 테스트: job 1/2/3 생성 → 2 삭제 → 새 job 생성 시 id가 "4"인지).

**프론트엔드** (수동 스모크, `frontend/README.md` 체크리스트에 추가):
- `history.html`에서 종료된 job 행에 "삭제" 버튼이 보이고, 클릭 → 확인 다이얼로그 → 목록에서 사라지는지.
- 진행 중인 job 행에는 "중지" 버튼만 보이는지, 클릭 후 상태가 `cancelled`로 바뀌면 "삭제" 버튼으로 전환되는지.
- `job.html`에서 두 취약점 표가 기본적으로 접힌 채로 로드되고, 제목 옆 건수 배지가 실제 행 수와 일치하는지, 클릭하면 펼쳐지는지.
