# 구현 계획 — 결과물 파일 트리 / 수정 전후 비교 화면

스펙: [`docs/superpowers/specs/2026-08-10-artifact-file-tree-viewer-design.md`](../specs/2026-08-10-artifact-file-tree-viewer-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아(`skills-lock.json`에 `brainstorming`만 등록됨) 이 문서는 기존 `2026-08-08-job-cancellation-plan.md` 형식을 그대로 따라 직접 작성했다. 단계는 의존성 순서(git 래퍼 함수 → API 엔드포인트 → job.html 진입 링크 → 신규 화면 → 테스트)를 따른다. 각 단계 뒤에 "검증"을 명시했으니, 구현 중 막히면 이전 단계로 돌아가지 말고 해당 단계의 검증부터 다시 확인한다.

## 0. 사전 확인

- 현재 `git status`가 깨끗한지 확인하고 시작한다(Plan 1 작업과 섞이지 않도록 — Plan 1을 먼저 구현·커밋한 뒤 이 계획을 시작하는 것을 권장).
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 기존 테스트가 전부 통과하는 베이스라인을 확인한다.

## 1. `checkpoint/git_repo.py` — 트리/파일 조회용 git 래퍼

기존 `resolve_ingest_baseline`/`diff_since` 아래에 추가 (스펙 §백엔드 설계의 코드 그대로):

```python
def list_tracked_files(work_dir: Path, settings: Settings) -> list[str]:
    """git ls-files 결과: HEAD에 커밋된 추적 파일 전체 (work/ 자체
    .gitignore가 이미 반영되어 있음)."""
    env = build_subprocess_env(settings)
    return [line for line in _run_git(work_dir, ["ls-files"], env).stdout.splitlines() if line.strip()]


def diff_status_map(work_dir: Path, settings: Settings, baseline_sha: str) -> dict[str, str]:
    """baseline..HEAD 사이에 추가(A)/수정(M)된 경로 -> 상태 코드. --no-renames로
    이름변경을 삭제+추가 쌍으로 단순화한다. 삭제(D)는 반환값에서 자연히
    제외된다 -- 삭제된 경로는 HEAD에 없으므로 list_tracked_files()에도 안
    나온다."""
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
    None."""
    env = build_subprocess_env(settings)
    executable = resolve_executable("git")
    proc = subprocess.run(
        [executable, "show", f"{ref}:{path}"], cwd=work_dir, capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
```

`subprocess`, `resolve_executable`은 이미 파일 상단에 import돼 있음. `show_file_bytes`는 기존 `_run_git`(text 모드)을 쓰지 않고 별도 bytes 모드 호출을 쓴다 — 바이너리 내용을 UTF-8로 강제 디코딩하면 깨지기 때문.

**검증**: `backend/tests/unit/test_git_repo.py`에 추가 (기존 `_settings()` 헬퍼, `git_init_and_baseline_commit`/`commit_checkpoint` 패턴 재사용):
- `test_list_tracked_files_reflects_head`: baseline 커밋 후 파일을 추가/커밋하면 목록에 반영되는지.
- `test_diff_status_map_excludes_deleted_files`: 파일 추가/수정/삭제를 섞은 커밋에서 A/M만 반환되고 D 경로는 결과에 없는지.
- `test_show_file_bytes_returns_none_for_missing_ref`: baseline엔 없고 HEAD에만 있는(새로 추가된) 파일 — baseline 시점 조회 시 `None`, HEAD 시점 조회 시 실제 내용.
- `test_show_file_bytes_preserves_binary_content`: `\x00`이 포함된 바이트 내용을 커밋하고 그대로(손상 없이) 반환되는지.

## 2. `api/routers/artifacts.py` — 트리/파일 엔드포인트

- import 추가: `from app.checkpoint.git_repo import diff_status_map, list_tracked_files, resolve_ingest_baseline, show_file_bytes`.
- 모듈 상단에 `_NOISE_DIR_NAMES = {".git", "target", "dist", "build", "node_modules", "__pycache__", ".venv"}` 추가.
- 기존 `_output_dir` 옆에 `_work_dir` 헬퍼 추가:

```python
def _work_dir(job_id: str, settings: Settings, db) -> Path:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return Path(settings.jobs_dir) / job_id / "work"
```

`Job` import가 이 파일에 아직 없으면 `from app.models.job import Job` 추가.

- 두 엔드포인트를 (스펙 §백엔드 설계의 코드 그대로) `get_handoff_guide` 아래에 추가:

```python
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

**검증**: `backend/tests/integration/test_artifacts_api.py`에 추가 (기존 `app_client`/`_wait_for_terminal_status`/`_zip_bytes` 재사용, `_POM` 픽스처를 여러 파일이 있는 프로젝트로 확장하거나 새 zip 픽스처 구성):
- `test_get_file_tree_marks_modified_and_excludes_noise_dirs`: 완주한 job에서 `GET /jobs/{id}/artifacts/tree` → 수정된 파일은 `status: "modified"`(또는 API가 반환하는 실제 코드값 `"M"`/`"modified"` 중 스펙과 일치하는 쪽으로 통일 — 구현 시 `diff_status_map`이 반환하는 원시 코드(`"A"`/`"M"`)를 그대로 쓸지, `"added"`/`"modified"`/`"unchanged"`로 변환할지 정하고 프론트(§6)와 맞춘다), 손대지 않은 파일은 `unchanged`, `target/` 등을 흉내 낸 파일이 있다면 목록에서 빠지는지.
- `test_get_file_content_returns_before_and_after`: 수정된 파일 하나를 골라 `GET /jobs/{id}/artifacts/file?path=...` → `before`/`after`가 실제 내용과 일치하는지.
- `test_get_file_content_for_unknown_path_returns_404`.
- `test_get_file_tree_and_content_for_unknown_job_returns_404`.

## 3. `job.html` — 결과물 영역 진입 링크

- `artifacts-panel`의 `artifact-buttons` div 안, `view-report-btn` 뒤에 추가:

```html
<a id="view-files-link" href="#" class="hidden">파일별로 보기</a>
```

**검증**: 브라우저에서 마크업만 확인(다음 단계에서 wiring).

## 4. `assets/job-view.js` — 링크 wiring

- 상단 참조에 `const viewFilesLink = el("view-files-link");` 추가.
- `loadArtifacts(jobId)` 안, `viewReportBtn` 설정 블록 뒤에 추가:

```javascript
if (body.diff) {
  viewFilesLink.href = `files.html?job=${encodeURIComponent(jobId)}`;
  viewFilesLink.classList.remove("hidden");
} else {
  viewFilesLink.classList.add("hidden");
}
```

**검증**: `node --check frontend/assets/job-view.js`. 실사용 확인은 §9.

## 5. `files.html` + `assets/files.js` — 신규 화면

- `frontend/files.html` 신규 생성 (스펙 §프론트엔드 설계의 마크업 그대로, `job.html`과 같은 `<header>`/`<nav>` 패턴 재사용):

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>파일 보기 - Maven Stack Upgrade Tool</title>
  <link rel="stylesheet" href="assets/app.css" />
</head>
<body>
  <header>
    <h1>Maven Stack Upgrade Tool</h1>
    <p class="subtitle">수정 전/후 코드를 파일별로 비교합니다</p>
    <nav class="page-nav"><a href="index.html">새 작업</a> · <a href="history.html">이력 보기</a></nav>
  </header>
  <main class="files-layout">
    <section id="tree-panel" class="card">
      <h2>파일 트리</h2>
      <p id="tree-error" class="error hidden"></p>
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
  <script src="assets/common.js"></script>
  <script src="assets/files.js"></script>
</body>
</html>
```

- `frontend/assets/files.js` 신규 생성 (`job.js`의 `URLSearchParams` job-id 파싱 패턴 재사용):

```javascript
"use strict";

const treeError = el("tree-error");
const fileTree = el("file-tree");
const fileViewerPanel = el("file-viewer-panel");
const fileViewerTitle = el("file-viewer-title");
const fileBefore = el("file-before");
const fileAfter = el("file-after");

const jobId = new URLSearchParams(location.search).get("job");

function buildTree(entries) {
  const root = {};
  for (const { path, status } of entries) {
    const parts = path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      if (isFile) {
        node[part] = { __file: true, path, status };
      } else {
        node[part] = node[part] || {};
        node = node[part];
      }
    });
  }
  return root;
}

function renderNode(name, node, container) {
  if (node.__file) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tree-file";
    btn.textContent = node.status === "unchanged" ? name : `${name} [${node.status}]`;
    btn.addEventListener("click", () => loadFileDiff(node.path));
    container.appendChild(btn);
    return;
  }
  const details = document.createElement("details");
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = name;
  details.appendChild(summary);
  Object.keys(node)
    .sort()
    .forEach((key) => renderNode(key, node[key], details));
  container.appendChild(details);
}

async function loadTree() {
  const res = await fetch(apiUrl(`/jobs/${encodeURIComponent(jobId)}/artifacts/tree`), { headers: authHeaders() });
  if (!res.ok) {
    treeError.textContent = `파일 트리를 불러오지 못했습니다 (HTTP ${res.status})`;
    treeError.classList.remove("hidden");
    return;
  }
  const entries = await res.json();
  const tree = buildTree(entries);
  fileTree.innerHTML = "";
  Object.keys(tree)
    .sort()
    .forEach((key) => renderNode(key, tree[key], fileTree));
}

async function loadFileDiff(path) {
  fileViewerPanel.classList.remove("hidden");
  fileViewerTitle.textContent = path;
  const res = await fetch(
    apiUrl(`/jobs/${encodeURIComponent(jobId)}/artifacts/file?path=${encodeURIComponent(path)}`),
    { headers: authHeaders() }
  );
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

if (!jobId) {
  treeError.textContent = "job id가 없습니다.";
  treeError.classList.remove("hidden");
} else {
  loadTree();
}
```

**검증**: `node --check frontend/assets/files.js`.

## 6. `assets/app.css` — 레이아웃 스타일

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
.tree-file {
  display: block;
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
  padding: 0.15rem 0;
}
```

`.tree-file`은 스펙에 명시되지 않았던 세부사항 — 트리의 파일 leaf가 클릭 가능한 `<button>`이라는 것만 스펙에 있고 구체적 스타일은 구현 시 정한다(목록처럼 보이도록 최소 스타일만).

**검증**: 브라우저에서 트리의 파일 항목이 버튼처럼 보이지 않고 텍스트 목록처럼 보이는지 육안 확인.

## 7. `frontend/README.md` — 수동 스모크 체크리스트 추가

스펙 §테스트 계획의 "프론트엔드" 목록을 기존 체크리스트 형식(`- [ ] ...`)에 맞춰 추가.

## 8. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인(0단계 베이스라인과 비교해 새로 깨진 테스트가 없는지).
- 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고, 실제 변경 사항이 있는 job으로:
  1. `job.html`에서 "파일별로 보기" 링크가 diff 있는 job에서만 보이는지.
  2. `files.html`에서 트리가 기본 펼침으로 로드되고 `target/`/`.git` 등이 안 보이는지.
  3. 수정된 파일 클릭 시 좌우에 실제 수정 전/후 코드가 보이는지, 새로 추가된 파일은 왼쪽에 "(새로 추가된 파일)"이 나오는지.
  4. (가능하면) 바이너리 파일이 포함된 프로젝트로 job을 하나 만들어 "바이너리 파일은 미리볼 수 없습니다" 문구가 뜨는지.

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

- `diff_status_map`이 반환하는 원시 상태 코드(`"A"`/`"M"`)를 트리 API 응답에 그대로 노출할지(`"A"`/`"M"`/`"unchanged"`), 프론트에서 읽기 좋은 값(`"added"`/`"modified"`/`"unchanged"`)으로 바꿀지는 스펙에 명시돼 있지 않다 — §2 구현 시 정하고, §5의 `files.js`가 그 값을 그대로 배지 텍스트(`[${node.status}]`)에 쓰므로 두 쪽을 반드시 같은 값으로 맞춘다.
- `files.js`의 트리 정렬은 `Object.keys(...).sort()`로 폴더/파일 구분 없이 알파벳순 — 폴더를 파일보다 먼저 보여주고 싶다면 `renderNode` 호출 전에 `node.__file` 유무로 먼저 나눠 정렬하는 것을 구현 시 고려(스펙에 없던 세부사항, 있으면 좋지만 필수는 아님).
