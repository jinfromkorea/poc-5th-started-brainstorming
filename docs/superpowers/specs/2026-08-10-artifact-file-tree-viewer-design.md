# 결과물 파일 트리 / 수정 전후 비교 화면

## 배경 및 목적

`job.html`의 "결과물" 영역은 지금 `patch.diff`(통합 diff 텍스트), `report.md`, handoff 가이드만 `<pre>` 하나에 통째로 보여준다. 어떤 파일이 어떻게 바뀌었는지 파일 단위로 훑어보려면 하나의 긴 unified diff를 눈으로 스크롤하며 찾아야 한다. `backend/data/jobs/{id}/work/`에는 마이그레이션이 끝난 전체 리포지토리가 그대로 남아 있지만, 이걸 파일별로 탐색할 수 있는 API/화면은 없다.

이 문서는 "결과물" 영역에서 새 화면으로 이동해, 프로젝트 디렉터리 구조를 트리로 보고 파일을 클릭하면 수정 전/후 코드를 나란히 비교하는 기능을 다룬다.

## 범위

- 백엔드: `GET /jobs/{id}/artifacts/tree`, `GET /jobs/{id}/artifacts/file` 엔드포인트, `checkpoint/git_repo.py`에 트리/파일 조회용 git 래퍼 함수 추가
- 프론트엔드: 신규 `files.html` + `assets/files.js`, `job.html` 결과물 영역에 진입 링크 추가

범위 밖: 변경된 파일만 필터링해서 보는 옵션(항상 전체 트리 표시), 파일 검색, 대용량 트리 페이지네이션/가상 스크롤, 신규 파일 diff 강조나 줄 단위 색상 표시.

## 결정 사항

- **`work/`를 파일시스템이 아니라 git으로 조회**: `work/`는 이미 `git init` + baseline 커밋으로 관리되는 저장소이고, 기존 `patch.diff`도 `git diff <ingest_baseline>..HEAD`로 만들어진다(`checkpoint/git_repo.py`). 같은 baseline을 기준으로 `git ls-files`/`git diff --name-status`/`git show`를 쓰면 파일시스템을 직접 순회하는 것보다 정확하고(예: 삭제된 파일이 자연히 빠짐), 기존 diff와 정확히 같은 기준선을 공유한다.
- **트리는 전체 디렉터리 구조**: 변경된 파일만이 아니라 프로젝트 전체 트리를 보여준다. 단 `.git`, `target`/`dist`/`build`/`node_modules`/`__pycache__`/`.venv` 등 빌드 산출물/도구 디렉터리는 제외한다 — 소스 코드가 아니고 job당 수십 MB에 달해 트리를 무의미하게 키운다.
- **삭제된 파일은 트리에서 제외**: 트리는 `work/`의 현재(마이그레이션 후) 상태를 그대로 반영한다. 삭제 이력은 기존 "diff 보기"(patch.diff)로 이미 확인 가능하므로 중복 기능을 만들지 않는다.
- **수정 전/후는 좌우 분할(side-by-side)**: 한 파일을 클릭하면 왼쪽에 baseline 시점 전체 코드, 오른쪽에 HEAD 시점 전체 코드를 나란히 보여준다. 줄 단위 강조(빨강/초록)는 1차 범위에서 제외 — 통합 diff는 이미 "diff 보기"에 있으므로, 이 화면은 "파일 전체를 원래 모습 그대로 비교"하는 보완적 뷰로 충분하다.
- **폴더 트리는 `<details>`/`<summary>`로 표현**: Spec 1(`2026-08-10-history-delete-and-analysis-collapse-design.md`)에서 도입한 접기/펼치기 패턴을 재사용해 일관성을 유지한다. 단 이 화면에서는 트리가 핵심 콘텐츠이므로 기본값은 **펼침**(취약점 표는 보조 정보라 기본 접힘이었던 것과 대비).

**[구현 후 변경]** 위 항목은 최초 설계였고, 이후 사용자 요청으로 트리 렌더링을 jsTree(jQuery 플러그인)로 교체했다. 이 프론트엔드는 지금까지 외부 의존성이 전혀 없는 순수 vanilla JS였고(CDN 스크립트도 전무), 이 도구가 사내 폐쇄망에서 개발자 PC에 직접 띄워 쓰는 로컬 도구라는 점을 고려해 CDN 대신 `frontend/assets/vendor/{jquery,jstree}/`에 jQuery 3.7.1 + jsTree 3.3.16(default 테마)를 다운로드해 커밋하는 방식으로 도입했다(다운로드 시 cdnjs가 제공하는 SRI 해시로 무결성 확인). 폴더 우선 정렬 + 기본 펼침 정책은 그대로 유지하며, jsTree의 `types` 플러그인으로 폴더/파일 아이콘만 구분한다. 첫 외부 라이브러리 도입이므로, 이후 다른 화면에서도 트리/리치 UI가 필요하면 이 vendor 디렉터리를 재사용할 수 있다.

## 백엔드 설계

### `checkpoint/git_repo.py` — 신규 함수

```python
def list_tracked_files(work_dir: Path, settings: Settings) -> list[str]:
    """git ls-files 결과: HEAD에 커밋된 추적 파일 전체 (work/ 자체
    .gitignore가 이미 반영되어 있음)."""
    env = build_subprocess_env(settings)
    return [line for line in _run_git(work_dir, ["ls-files"], env).stdout.splitlines() if line.strip()]


def diff_status_map(work_dir: Path, settings: Settings, baseline_sha: str) -> dict[str, str]:
    """baseline..HEAD 사이에 추가(A)/수정(M)된 경로 -> 상태 코드. --no-renames로
    이름변경을 삭제+추가 쌍으로 단순화한다 (트리 뷰어는 파일별 상태 배지만
    필요, 이름변경 추적은 범위 밖). 삭제(D)는 반환값에서 자연히 제외된다 --
    삭제된 경로는 HEAD에 없으므로 list_tracked_files()에도 안 나온다."""
    env = build_subprocess_env(settings)
    out = _run_git(work_dir, ["diff", "--name-status", "--no-renames", baseline_sha, "HEAD"], env).stdout
    result: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        status_code, path = line.split("\t", 1)
        if status_code in ("A", "M"):
            result[path] = status_code
    return result


def show_file_bytes(work_dir: Path, settings: Settings, ref: str, path: str) -> bytes | None:
    """git show {ref}:{path}의 raw bytes. 그 ref 시점에 해당 경로가 없으면
    None (예: 새로 추가된 파일의 baseline 시점 내용, 또는 그 반대)."""
    env = build_subprocess_env(settings)
    executable = resolve_executable("git")
    proc = subprocess.run(
        [executable, "show", f"{ref}:{path}"], cwd=work_dir, capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
```

`show_file_bytes`는 `_run_git`(text 모드)을 쓰지 않고 별도로 bytes 모드 subprocess를 호출한다 — 바이너리 파일 내용을 UTF-8로 강제 디코딩하지 않기 위해서다.

### `api/routers/artifacts.py` — 신규 엔드포인트

```python
_NOISE_DIR_NAMES = {".git", "target", "dist", "build", "node_modules", "__pycache__", ".venv"}


def _work_dir(job_id: str, settings: Settings, db) -> Path:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return Path(settings.jobs_dir) / job_id / "work"


@router.get("/{job_id}/artifacts/tree")
async def get_file_tree(job_id: str, settings: Settings = Depends(get_settings), db=Depends(get_db_session)) -> list[dict]:
    work_dir = _work_dir(job_id, settings, db)
    baseline = resolve_ingest_baseline(work_dir, settings)
    status_map = diff_status_map(work_dir, settings, baseline)
    return [
        {"path": p, "status": status_map.get(p, "unchanged")}
        for p in list_tracked_files(work_dir, settings)
        if not any(seg in _NOISE_DIR_NAMES for seg in p.split("/"))
    ]


@router.get("/{job_id}/artifacts/file")
async def get_file_before_after(
    job_id: str, path: str, settings: Settings = Depends(get_settings), db=Depends(get_db_session)
) -> dict:
    work_dir = _work_dir(job_id, settings, db)
    # 화이트리스트 검증: 실제 추적 파일 목록에 있는 경로만 허용 -- 경로 조작
    # 방지 (get_handoff_guide의 기존 파일명 화이트리스트와 동일 패턴).
    if path not in set(list_tracked_files(work_dir, settings)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown file path: {path}")

    baseline = resolve_ingest_baseline(work_dir, settings)
    before = show_file_bytes(work_dir, settings, baseline, path)
    after = show_file_bytes(work_dir, settings, "HEAD", path)
    binary = bool((before and b"\x00" in before) or (after and b"\x00" in after))

    def _decode(raw: bytes | None) -> str | None:
        if raw is None or binary:
            return None
        return raw.decode("utf-8", errors="replace")

    return {"before": _decode(before), "after": _decode(after), "binary": binary}
```

두 엔드포인트 모두 기존 `_output_dir` 대신 `_work_dir`을 쓰는 것만 다르고, 404 처리(`job_id` 미존재)는 동일 패턴.

## 프론트엔드 설계

### `job.html` — 진입 링크

```html
<a id="view-files-link" href="#" class="hidden">파일별로 보기</a>
```

기존 `loadArtifacts(jobId)`에서 `artifacts.diff`가 true일 때(diff/report 버튼과 같은 조건) `view-files-link.href = "files.html?job=" + encodeURIComponent(jobId)`로 설정하고 `hidden` 해제. diff가 없는 job(예: 변경 사항 없음, 취소됨)에서는 노출하지 않는다.

### `files.html` (신규)

```html
<main class="files-layout">
  <section id="tree-panel" class="card">
    <h2>파일 트리</h2>
    <div id="file-tree"></div>
  </section>
  <section id="file-viewer-panel" class="card hidden">
    <h2 id="file-viewer-title"></h2>
    <div class="split-view">
      <div class="split-pane">
        <h3>수정 전</h3>
        <pre id="file-before"></pre>
      </div>
      <div class="split-pane">
        <h3>수정 후</h3>
        <pre id="file-after"></pre>
      </div>
    </div>
  </section>
</main>
```

### `assets/files.js` (신규)

- URL 쿼리 `?job=`으로 job id를 읽는다 (`job.js`가 이미 하는 방식과 동일).
- `GET /jobs/{id}/artifacts/tree` 호출 → 평평한 `{path, status}[]` 응답을 받아 `/`로 쪼개 중첩 객체로 조립. 트리 구성 로직은 프론트에 두고 백엔드는 평평한 목록만 반환 (`history.js`/`job-view.js`가 항상 렌더링을 JS에 두는 기존 패턴과 동일).
- 폴더는 `<details open>`/`<summary>`로 재귀 렌더링 (Spec 1에서 도입한 패턴 재사용, 기본 펼침). 파일은 클릭 가능한 `<button>`에 상태 배지(`M`/`A`, `unchanged`는 배지 없음)를 붙인다.
- 파일 클릭 시:

```javascript
async function loadFileDiff(path) {
  fileViewerPanel.classList.remove("hidden");
  fileViewerTitle.textContent = path;
  const res = await fetch(apiUrl(`/jobs/${jobId}/artifacts/file?path=${encodeURIComponent(path)}`), {
    headers: authHeaders(),
  });
  if (!res.ok) {
    fileBefore.textContent = fileAfter.textContent = `불러오지 못했습니다 (HTTP ${res.status})`;
    return;
  }
  const data = await res.json();
  if (data.binary) {
    fileBefore.textContent = fileAfter.textContent = "바이너리 파일은 미리볼 수 없습니다.";
    return;
  }
  fileBefore.textContent = data.before ?? "(새로 추가된 파일)";
  fileAfter.textContent = data.after ?? "(삭제된 파일)";
}
```

### `assets/app.css`

```css
.files-layout {
  display: flex;
  gap: 1rem;
}
.split-view {
  display: flex;
  gap: 1rem;
}
.split-pane {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
}
```

데스크톱 전용 단순 flex 레이아웃 — 반응형 대응은 범위 밖 (기존 프론트엔드도 반응형을 고려하지 않음).

## 에러 처리 / 엣지 케이스

- 존재하지 않는 job_id → `/tree`, `/file` 모두 404.
- `path` 쿼리가 실제 추적 파일 목록에 없는 경우(오타, 경로 조작 시도) → 404.
- 새로 추가된 파일 → `before: null`, 프론트에서 "(새로 추가된 파일)" 표시.
- 바이너리 파일(이미지, jar 등 `.gitignore`에 안 걸려서 커밋된 경우) → `binary: true`, 내용 대신 안내 문구.
- diff가 없는 job(변경 없음, 취소됨) → `job.html`에서 "파일별로 보기" 링크 자체가 노출되지 않음(diff 버튼과 동일 게이트).
- 매우 큰 트리/파일 → 페이지네이션 없음(범위 밖) — 기존 `history.html`도 "1인 개발자 로컬 도구, job 수가 많지 않다고 가정"하고 페이지네이션을 두지 않은 것과 같은 전제.

## 테스트 계획

**단위** (`backend/tests/unit/test_git_repo.py`에 추가):
- `list_tracked_files`: baseline 커밋 후 파일 추가/커밋 시 목록에 반영되는지.
- `diff_status_map`: 파일 추가/수정/삭제를 섞은 커밋에서 A/M만 반환되고 D는 빠지는지.
- `show_file_bytes`: 존재하는 경로는 해당 ref의 내용을, 그 시점에 없던 경로는 `None`을 반환하는지. 바이너리(`\x00` 포함) 내용도 손상 없이 그대로 반환하는지.

**통합** (`backend/tests/integration/test_artifacts_api.py`에 추가):
- `GET /jobs/{id}/artifacts/tree`: 변경/미변경 파일 상태가 올바르고, `target/` 등 노이즈 디렉터리가 빠지는지.
- `GET /jobs/{id}/artifacts/file?path=...`: 수정된 파일의 before/after가 올바르고, 추가된 파일은 `before: null`인지.
- 목록에 없는 `path` 요청 → 404.
- 존재하지 않는 job_id → 두 엔드포인트 모두 404.

**프론트엔드** (수동 스모크, `frontend/README.md` 체크리스트에 추가):
- `job.html`에서 diff가 있는 job에 "파일별로 보기" 링크가 보이고, 클릭 시 `files.html?job={id}`로 이동하는지.
- `files.html`에서 트리가 기본 펼침 상태로 로드되고, `target/` 등이 안 보이는지.
- 수정된 파일 클릭 시 좌우에 수정 전/후 코드가 올바르게 나오는지, 새로 추가된 파일은 왼쪽에 "(새로 추가된 파일)"이 나오는지.
