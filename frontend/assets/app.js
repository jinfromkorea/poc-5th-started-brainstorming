"use strict";

const TERMINAL_STATUSES = new Set(["success", "needs_handoff", "failed"]);
const STORAGE_KEYS = { apiBase: "ace-upgrade-tool.apiBase", apiToken: "ace-upgrade-tool.apiToken" };
const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const el = (id) => document.getElementById(id);

const apiBaseInput = el("api-base");
const apiTokenInput = el("api-token");
const jobForm = el("job-form");
const submitBtn = el("submit-btn");
const formError = el("form-error");
const gitFields = el("git-fields");
const zipFields = el("zip-fields");
const gitUrlInput = el("git-url");
const gitRefInput = el("git-ref");
const zipFileInput = el("zip-file");
const outputVersionInput = el("output-version");
const runStage1Checkbox = el("run-stage1");
const runStage2Checkbox = el("run-stage2");
const progressPanel = el("progress-panel");
const jobIdDisplay = el("job-id-display");
const statusBadge = el("status-badge");
const logList = el("log-list");
const artifactsPanel = el("artifacts-panel");
const viewDiffBtn = el("view-diff-btn");
const viewReportBtn = el("view-report-btn");
const handoffList = el("handoff-list");
const artifactViewer = el("artifact-viewer");
const artifactViewerTitle = el("artifact-viewer-title");
const artifactViewerContent = el("artifact-viewer-content");
const copyArtifactBtn = el("copy-artifact-btn");
const downloadArtifactBtn = el("download-artifact-btn");

let currentEventSource = null;

function getApiBase() {
  return (apiBaseInput.value || DEFAULT_API_BASE).trim().replace(/\/$/, "");
}

function getApiToken() {
  return apiTokenInput.value.trim();
}

function authHeaders() {
  const token = getApiToken();
  return token ? { "X-API-Token": token } : {};
}

function apiUrl(path) {
  return `${getApiBase()}${path}`;
}

function loadConnectionSettings() {
  apiBaseInput.value = localStorage.getItem(STORAGE_KEYS.apiBase) || DEFAULT_API_BASE;
  apiTokenInput.value = localStorage.getItem(STORAGE_KEYS.apiToken) || "";
}

apiBaseInput.addEventListener("input", () => localStorage.setItem(STORAGE_KEYS.apiBase, apiBaseInput.value));
apiTokenInput.addEventListener("input", () => localStorage.setItem(STORAGE_KEYS.apiToken, apiTokenInput.value));

document.querySelectorAll('input[name="source-type"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const isGit = document.querySelector('input[name="source-type"]:checked').value === "git";
    gitFields.classList.toggle("hidden", !isGit);
    zipFields.classList.toggle("hidden", isGit);
  });
});

function appendLog(text, isError = false) {
  const line = document.createElement("div");
  line.textContent = text;
  if (isError) line.classList.add("log-error");
  logList.appendChild(line);
  logList.scrollTop = logList.scrollHeight;
}

function setStatusBadge(status) {
  statusBadge.textContent = status;
  statusBadge.className = `badge status-${status}`;
}

function connectSSE(jobId) {
  if (currentEventSource) {
    currentEventSource.close();
  }
  const token = getApiToken();
  const query = token ? `?api_token=${encodeURIComponent(token)}` : "";
  const es = new EventSource(apiUrl(`/jobs/${jobId}/events${query}`));
  currentEventSource = es;

  es.addEventListener("log", (ev) => {
    const data = JSON.parse(ev.data);
    appendLog(data.message);
  });

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    setStatusBadge(data.status);
    if (data.error) {
      appendLog(`오류: ${data.error}`, true);
    }
    if (TERMINAL_STATUSES.has(data.status)) {
      es.close();
      loadArtifacts(jobId);
    }
  });

  es.addEventListener("error", (ev) => {
    if (ev.data) {
      try {
        const data = JSON.parse(ev.data);
        appendLog(`오류: ${data.message}`, true);
      } catch (parseErr) {
        appendLog(`오류: ${ev.data}`, true);
      }
    }
  });

  es.onerror = () => {
    // EventSource auto-retries on network-level errors unless explicitly
    // closed; we only close it above once a terminal "status" event has
    // been seen, so a transient reconnect here is expected behavior.
    appendLog("[연결 재시도 중...]");
  };
}

async function loadArtifacts(jobId) {
  artifactsPanel.classList.remove("hidden");
  const res = await fetch(apiUrl(`/jobs/${jobId}/artifacts`), { headers: authHeaders() });
  if (!res.ok) {
    appendLog(`결과물 목록을 불러오지 못했습니다 (HTTP ${res.status})`, true);
    return;
  }
  const body = await res.json();

  viewDiffBtn.disabled = !body.diff;
  viewDiffBtn.onclick = () => showArtifact("diff (patch.diff)", apiUrl(`/jobs/${jobId}/artifacts/diff`), "patch.diff");

  viewReportBtn.disabled = !body.report;
  viewReportBtn.onclick = () => showArtifact("report (report.md)", apiUrl(`/jobs/${jobId}/artifacts/report`), "report.md");

  handoffList.innerHTML = "";
  body.handoff.forEach((name) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary";
    btn.textContent = name;
    btn.onclick = () => showArtifact(name, apiUrl(`/jobs/${jobId}/artifacts/handoff/${encodeURIComponent(name)}`), name);
    handoffList.appendChild(btn);
  });
}

async function showArtifact(title, url, downloadName) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    appendLog(`${title}을(를) 불러오지 못했습니다 (HTTP ${res.status})`, true);
    return;
  }
  const text = await res.text();

  artifactViewer.classList.remove("hidden");
  artifactViewerTitle.textContent = title;
  artifactViewerContent.textContent = text;

  copyArtifactBtn.onclick = () => navigator.clipboard.writeText(text);
  downloadArtifactBtn.onclick = () => downloadText(downloadName, text);
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function resetProgressUI(jobId) {
  progressPanel.classList.remove("hidden");
  jobIdDisplay.textContent = jobId;
  setStatusBadge("queued");
  logList.innerHTML = "";
  artifactsPanel.classList.add("hidden");
  artifactViewer.classList.add("hidden");
}

jobForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  formError.classList.add("hidden");

  const sourceType = document.querySelector('input[name="source-type"]:checked').value;
  const fd = new FormData();

  if (sourceType === "git") {
    if (!gitUrlInput.value.trim()) {
      formError.textContent = "Git URL을 입력해주세요.";
      formError.classList.remove("hidden");
      return;
    }
    fd.append("git_url", gitUrlInput.value.trim());
    if (gitRefInput.value.trim()) fd.append("git_ref", gitRefInput.value.trim());
  } else {
    if (!zipFileInput.files[0]) {
      formError.textContent = "ZIP 파일을 선택해주세요.";
      formError.classList.remove("hidden");
      return;
    }
    fd.append("zip_file", zipFileInput.files[0]);
  }

  if (outputVersionInput.value.trim()) fd.append("output_version", outputVersionInput.value.trim());
  fd.append("run_stage1", runStage1Checkbox.checked ? "true" : "false");
  fd.append("run_stage2", runStage2Checkbox.checked ? "true" : "false");

  submitBtn.disabled = true;
  submitBtn.textContent = "제출 중...";

  try {
    const res = await fetch(apiUrl("/jobs"), { method: "POST", headers: authHeaders(), body: fd });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const body = await res.json();
    resetProgressUI(body.job_id);
    connectSSE(body.job_id);
  } catch (err) {
    formError.textContent = `작업 시작 실패: ${err.message}`;
    formError.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "작업 시작";
  }
});

loadConnectionSettings();
