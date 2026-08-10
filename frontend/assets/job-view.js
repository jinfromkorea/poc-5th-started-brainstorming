"use strict";

const TERMINAL_STATUSES = new Set(["success", "needs_handoff", "failed", "cancelled"]);
const SEVERITY_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, UNKNOWN: 4 };

const analysisPanel = el("analysis-panel");
const stackInfo = el("stack-info");
const stackJava = el("stack-java");
const stackSpringBoot = el("stack-spring-boot");
const stackSpringCloud = el("stack-spring-cloud");
const stackSpringAi = el("stack-spring-ai");
const vulnBaselineSection = el("vuln-baseline-section");
const vulnBaselineTableBody = el("vuln-baseline-table-body");
const vulnBaselineEmpty = el("vuln-baseline-empty");
const vulnBaselineCount = el("vuln-baseline-count");
const vulnSection = el("vuln-section");
const vulnTableBody = el("vuln-table-body");
const vulnEmpty = el("vuln-empty");
const vulnCount = el("vuln-count");

const progressPanel = el("progress-panel");
const jobIdDisplay = el("job-id-display");
const statusBadge = el("status-badge");
const proceedBtn = el("proceed-btn");
const stopBtn = el("stop-btn");
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

function renderInventory(detected) {
  analysisPanel.classList.remove("hidden");
  stackInfo.classList.remove("hidden");
  stackJava.textContent = detected.java_version || "감지 안됨";
  stackSpringBoot.textContent = detected.spring_boot_version || "감지 안됨";
  stackSpringCloud.textContent = detected.spring_cloud_version || "감지 안됨";
  stackSpringAi.textContent = detected.spring_ai_version || "감지 안됨";
}

function renderVulnerabilitiesInto(vulnerabilities, { section, tableBody, emptyMsg, countBadge }) {
  analysisPanel.classList.remove("hidden");
  section.classList.remove("hidden");
  countBadge.textContent = `${vulnerabilities.length}건`;

  tableBody.innerHTML = "";
  if (vulnerabilities.length === 0) {
    emptyMsg.classList.remove("hidden");
    return;
  }
  emptyMsg.classList.add("hidden");

  const sorted = [...vulnerabilities].sort((a, b) => {
    const bySeverity = (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99);
    if (bySeverity !== 0) return bySeverity;
    return (b.cvss || 0) - (a.cvss || 0);
  });

  sorted.forEach((v) => {
    const row = document.createElement("tr");
    const severityClass = (v.severity || "unknown").toLowerCase();

    const cveCell = document.createElement("td");
    cveCell.textContent = v.cve_id;
    const packageCell = document.createElement("td");
    packageCell.textContent = v.package;
    const installedCell = document.createElement("td");
    installedCell.textContent = v.installed_version;
    const fixCell = document.createElement("td");
    fixCell.textContent = v.fix_version || "-";
    const cvssCell = document.createElement("td");
    cvssCell.textContent = v.cvss != null ? v.cvss.toFixed(1) : "-";
    const severityCell = document.createElement("td");
    const severityBadge = document.createElement("span");
    severityBadge.className = `badge severity-${severityClass}`;
    severityBadge.textContent = v.severity || "UNKNOWN";
    severityCell.appendChild(severityBadge);

    row.append(cveCell, packageCell, installedCell, fixCell, cvssCell, severityCell);
    tableBody.appendChild(row);
  });
}

function renderVulnerabilitiesBaseline(vulnerabilities) {
  renderVulnerabilitiesInto(vulnerabilities, {
    section: vulnBaselineSection,
    tableBody: vulnBaselineTableBody,
    emptyMsg: vulnBaselineEmpty,
    countBadge: vulnBaselineCount,
  });
}

function renderVulnerabilities(vulnerabilities) {
  renderVulnerabilitiesInto(vulnerabilities, {
    section: vulnSection,
    tableBody: vulnTableBody,
    emptyMsg: vulnEmpty,
    countBadge: vulnCount,
  });
}

function showProceedButton(jobId) {
  proceedBtn.classList.remove("hidden");
  proceedBtn.disabled = false;
  proceedBtn.textContent = "2단계로 진행 (승인)";
  proceedBtn.onclick = async () => {
    proceedBtn.disabled = true;
    proceedBtn.textContent = "진행 중...";
    try {
      const res = await fetch(apiUrl(`/jobs/${jobId}/proceed`), { method: "POST", headers: authHeaders() });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      proceedBtn.classList.add("hidden");
      // no reconnect needed -- the SSE connection is still open (awaiting_approval
      // isn't terminal) and will receive Stage 2's events live as they happen.
    } catch (err) {
      appendLog(`2단계 진행 요청 실패: ${err.message}`, true);
      proceedBtn.disabled = false;
      proceedBtn.textContent = "2단계로 진행 (승인)";
    }
  };
}

function showStopButton(jobId) {
  stopBtn.classList.remove("hidden");
  stopBtn.disabled = false;
  stopBtn.textContent = "중지";
  stopBtn.onclick = async () => {
    if (!confirm("정말 이 작업을 중지할까요? 지금까지의 변경 내용은 저장되지 않습니다.")) return;
    stopBtn.disabled = true;
    stopBtn.textContent = "중지 중...";
    try {
      const res = await fetch(apiUrl(`/jobs/${jobId}/cancel`), { method: "POST", headers: authHeaders() });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      // no reconnect needed -- the already-open SSE connection delivers the
      // confirmed "cancelled" status event once cleanup actually finishes.
    } catch (err) {
      appendLog(`작업 중지 요청 실패: ${err.message}`, true);
      stopBtn.disabled = false;
      stopBtn.textContent = "중지";
    }
  };
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

  es.addEventListener("inventory", (ev) => {
    renderInventory(JSON.parse(ev.data));
  });

  es.addEventListener("vulnerabilities_baseline", (ev) => {
    renderVulnerabilitiesBaseline(JSON.parse(ev.data).vulnerabilities);
  });

  es.addEventListener("vulnerabilities", (ev) => {
    renderVulnerabilities(JSON.parse(ev.data).vulnerabilities);
  });

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    setStatusBadge(data.status);
    if (data.error) {
      appendLog(`오류: ${data.error}`, true);
    }
    if (data.status === "awaiting_approval") {
      // let the human review the diff/report/handoff guide so far before deciding
      loadArtifacts(jobId);
      showProceedButton(jobId);
    } else {
      proceedBtn.classList.add("hidden");
    }
    if (TERMINAL_STATUSES.has(data.status)) {
      stopBtn.classList.add("hidden");
      es.close();
      loadArtifacts(jobId);
    } else {
      showStopButton(jobId);
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
