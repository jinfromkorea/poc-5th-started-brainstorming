"use strict";

const treeError = el("tree-error");
const expandAllBtn = el("expand-all-btn");
const collapseAllBtn = el("collapse-all-btn");
const fileViewerPanel = el("file-viewer-panel");
const fileViewerTitle = el("file-viewer-title");
const fileBefore = el("file-before");
const fileAfter = el("file-after");

const jobId = new URLSearchParams(location.search).get("job");

// Builds jsTree's nested JSON node format ({text, type, children}) from the
// flat {path, status}[] the API returns. A plain object keyed by path
// segment is used as scratch space while assembling the tree, then
// converted (folders-before-files, each group alphabetical -- file-explorer
// style) into the array jsTree expects.
function buildTreeData(entries) {
  const root = { children: {} };
  for (const { path, status } of entries) {
    const parts = path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      if (!node.children[part]) {
        node.children[part] = isFile
          ? { text: status === "unchanged" ? part : `${part} [${status}]`, type: "file", li_attr: { "data-path": path } }
          : { text: part, type: "default", children: {} };
      }
      node = node.children[part];
    });
  }

  function toNodeArray(node) {
    const folders = [];
    const files = [];
    Object.keys(node.children)
      .sort((a, b) => a.localeCompare(b))
      .forEach((key) => {
        const child = node.children[key];
        if (child.type === "file") {
          files.push(child);
        } else {
          child.children = toNodeArray(child);
          folders.push(child);
        }
      });
    return [...folders, ...files];
  }

  return toNodeArray(root);
}

async function loadTree() {
  const res = await fetch(apiUrl(`/jobs/${encodeURIComponent(jobId)}/artifacts/tree`), { headers: authHeaders() });
  if (!res.ok) {
    treeError.textContent = `파일 트리를 불러오지 못했습니다 (HTTP ${res.status})`;
    treeError.classList.remove("hidden");
    return;
  }
  const entries = await res.json();
  const treeData = buildTreeData(entries);

  $("#file-tree")
    .jstree({
      core: { data: treeData, themes: { icons: true } },
      types: { default: { icon: "jstree-folder" }, file: { icon: "jstree-file" } },
      plugins: ["types"],
    })
    .on("select_node.jstree", (_ev, data) => {
      const path = data.node.li_attr && data.node.li_attr["data-path"];
      if (path) loadFileDiff(path);
    });

  expandAllBtn.addEventListener("click", () => $("#file-tree").jstree("open_all"));
  collapseAllBtn.addEventListener("click", () => $("#file-tree").jstree("close_all"));
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
