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

function sortedChildKeys(node) {
  // Folders before files, each group alphabetical -- file-explorer style.
  return Object.keys(node).sort((a, b) => {
    const aIsFile = !!node[a].__file;
    const bIsFile = !!node[b].__file;
    if (aIsFile !== bIsFile) return aIsFile ? 1 : -1;
    return a.localeCompare(b);
  });
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
  // <details>/<summary> -- folder collapse/expand is native; open=true is
  // only the initial state, still togglable by clicking the summary.
  const details = document.createElement("details");
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = name;
  details.appendChild(summary);
  sortedChildKeys(node).forEach((key) => renderNode(key, node[key], details));
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
  sortedChildKeys(tree).forEach((key) => renderNode(key, tree[key], fileTree));
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
