# 작업 이력 화면 (Job History Screen)

## 배경 및 목적

현재 프론트엔드(`frontend/`)는 빌드 없는 단일 정적 HTML 페이지(`index.html`)로, "새 작업 시작 → 진행 상황 → 결과물 확인" 흐름만 지원한다. 과거에 실행한 job은 `job_id`를 기억하고 있지 않으면 다시 조회할 방법이 없다. 이 문서는 과거 job 목록을 조회하고, 각 job의 진행 로그/결과물을 다시 볼 수 있는 화면을 추가하는 설계를 다룬다.

## 범위

- 백엔드: job 전체 목록 조회 API 1개 추가
- 프론트엔드: 목록 페이지 1개, 상세 페이지 1개 추가, 기존 `app.js`의 일부 로직을 공용 모듈로 분리

## 백엔드: `GET /jobs` 목록 엔드포인트

`backend/app/api/routers/jobs.py`에 다음 엔드포인트를 추가한다.

```python
@router.get("", response_model=list[JobStatusResponse])
async def list_jobs(db=Depends(get_db_session)) -> list[JobStatusResponse]:
    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    return [
        JobStatusResponse(
            job_id=j.id,
            status=j.status,
            source_type=j.source_type,
            source_ref=j.source_ref,
            run_stage1=j.run_stage1,
            run_stage2=j.run_stage2,
            output_version=j.output_version,
            error_message=j.error_message,
            report_markdown=j.report_markdown,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )
        for j in jobs
    ]
```

- 기존 `JobStatusResponse` 스키마를 그대로 재사용한다 (새 스키마 불필요).
- 정렬: `created_at` 내림차순(최신 먼저) 고정. 필터링·페이지네이션은 두지 않는다 — 단일 개발자용 로컬 도구로 job 수가 많지 않을 것으로 가정(`next_job_id`의 기존 주석과 동일한 전제).
- 인증: 라우터 레벨에 이미 걸린 `require_api_token` 의존성을 그대로 사용.
- 라우트 등록 순서 주의: `GET /jobs/{job_id}`보다 먼저 선언되어야 `""`(목록) 경로가 `{job_id}` 패턴에 잡아먹히지 않는다. FastAPI는 선언 순서대로 매칭하므로 `list_jobs`를 `get_job`보다 위에 둔다.

## 프론트엔드: 페이지 구조

빌드 단계가 없는 순수 HTML/JS 프로젝트 방침을 유지한다. 새 페이지를 별도 HTML 파일로 추가한다 (탭 전환이 아닌 독립 페이지 방식을 선택함 — 라우팅 프레임워크 없이 각 화면의 책임을 파일 단위로 분리하는 편이 이 프로젝트의 "빌드 없음" 원칙과 더 잘 맞는다는 판단).

### 파일 구성

- `frontend/history.html` — 작업 이력 목록
- `frontend/job.html` — 개별 job 상세(진행 상황 + 결과물)
- `frontend/assets/common.js` — 연결 설정(API 주소/토큰) 로직 공용화
- `frontend/assets/job-view.js` — 진행 상황(SSE)·결과물 표시 로직 공용화
- `frontend/assets/history.js` — 이력 목록 페이지 전용 로직
- `frontend/assets/job.js` — job 상세 페이지 전용 로직 (URL 쿼리 파싱 + 초기 조회)

### 기존 코드 리팩토링

`app.js`에는 두 종류의 로직이 섞여 있다:

1. **연결 설정** — `getApiBase`, `getApiToken`, `authHeaders`, `apiUrl`, `loadConnectionSettings`, 입력값 변경 시 `localStorage` 저장. `index.html`, `history.html`, `job.html` 세 페이지 모두 필요.
2. **진행 상황/결과물 뷰** — `connectSSE`, `appendLog`, `setStatusBadge`, `loadArtifacts`, `showArtifact`, `downloadText`. `index.html`(새 작업 제출 직후)과 `job.html`(이력에서 진입) 양쪽에서 동일하게 필요 — 둘 다 "이 job_id의 진행 상황과 결과물을 보여준다"는 같은 일을 한다.

이 두 그룹을 각각 `common.js`, `job-view.js`로 추출하고, `app.js`에는 폼 제출·소스 타입 토글 등 `index.html` 고유 로직만 남긴다. `localStorage` 키(`STORAGE_KEYS`)는 페이지 간 공유되므로(같은 오리진) 연결 설정은 한 곳에서 입력하면 다른 페이지에도 자동 반영된다.

각 HTML은 필요한 스크립트를 순서대로 로드한다:

```html
<!-- index.html -->
<script src="assets/common.js"></script>
<script src="assets/job-view.js"></script>
<script src="assets/app.js"></script>

<!-- job.html -->
<script src="assets/common.js"></script>
<script src="assets/job-view.js"></script>
<script src="assets/job.js"></script>

<!-- history.html -->
<script src="assets/common.js"></script>
<script src="assets/history.js"></script>
```

### 페이지 간 이동

세 페이지 상단에 단순 텍스트 링크를 둔다 (예: `index.html`에는 "이력 보기" → `history.html`, `history.html`/`job.html`에는 "새 작업" → `index.html`).

## 화면 동작

### `history.html` — 이력 목록

- 로드 시 `GET /jobs`를 호출해 테이블로 렌더링. 컬럼: job_id, 상태 배지, 소스(git URL 또는 zip 파일명), output_version, 1/2단계 실행 여부, 생성/수정 시각.
- 상단에 "새로고침" 버튼 — 클릭 시 `GET /jobs` 재호출. 자동 폴링은 하지 않는다(수동 새로고침만).
- 각 행의 job_id 셀은 `job.html?job={id}` 링크.
- `GET /jobs` 실패 시 목록 영역에 인라인 에러 메시지 표시 (기존 `#form-error` 패턴과 동일한 스타일 재사용).

### `job.html` — 개별 job 상세

- URL 쿼리 `?job={id}`에서 job_id를 읽는다. 쿼리가 없거나 `GET /jobs/{id}`가 404를 반환하면 "존재하지 않는 작업입니다" 안내와 함께 이력 목록으로 돌아가는 링크를 표시한다.
- 유효한 job_id면:
  - `GET /jobs/{id}`로 기본 정보(상태, 소스, output_version 등) 표시
  - `job-view.js`의 `connectSSE(jobId)` 재사용 — `GET /jobs/{id}/events`에 연결. 이미 종료된 job이면 서버가 이력을 replay한 뒤 스트림을 바로 닫으므로 자연스럽게 "로그 히스토리 보기"로 동작하고, running/queued job이면 실시간 로그를 이어서 보여준다 (백엔드 `stream_job_events`가 이미 이 두 경우를 모두 처리함 — 새 백엔드 로직 불필요).
  - 종료 상태(success/needs_handoff/failed) 도달 시 `loadArtifacts(jobId)` 재사용 — diff/report/handoff 조회 및 복사·다운로드.

## 테스트

**백엔드** (`backend/tests/integration/test_jobs_api.py`에 추가):
- job이 없을 때 `GET /jobs` → 빈 배열
- 여러 job 생성 후 `GET /jobs` → `created_at` 내림차순으로 반환되는지, 각 필드가 `JobStatusResponse`와 일치하는지
- 인증 토큰 설정 시 `GET /jobs`도 인증이 걸리는지 (기존 라우터 인증 테스트와 동일 패턴)

**프론트엔드** (자동화 테스트 없는 프로젝트 방침 유지, `frontend/README.md`의 수동 스모크 테스트 체크리스트에 항목 추가):
- 이력 목록이 최신순으로 표시되는지, "새로고침" 버튼이 동작하는지
- job_id 클릭 시 상세 페이지로 이동하고 올바른 job 정보가 표시되는지
- 종료된 job의 상세 페이지에서 로그 히스토리와 결과물(diff/report/handoff)이 정상 표시되는지
- 진행 중인 job의 상세 페이지에서 실시간 로그가 이어지는지
- 잘못된/존재하지 않는 job_id로 상세 페이지 접근 시 에러 안내와 목록으로 돌아가는 링크가 표시되는지
- `index.html`, `history.html`, `job.html` 간 이동 링크가 모두 동작하고, 연결 설정(API 주소/토큰)이 페이지 간 유지되는지
