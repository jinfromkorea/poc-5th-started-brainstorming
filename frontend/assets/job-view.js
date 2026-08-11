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
const vulnPostStage1Section = el("vuln-post-stage1-section");
const vulnPostStage1TableBody = el("vuln-post-stage1-table-body");
const vulnPostStage1Empty = el("vuln-post-stage1-empty");
const vulnPostStage1Count = el("vuln-post-stage1-count");
const vulnSection = el("vuln-section");
const vulnTableBody = el("vuln-table-body");
const vulnEmpty = el("vuln-empty");
const vulnCount = el("vuln-count");
const vulnFinalSection = el("vuln-final-section");
const vulnFinalTableBody = el("vuln-final-table-body");
const vulnFinalEmpty = el("vuln-final-empty");
const vulnFinalCount = el("vuln-final-count");

const progressPanel = el("progress-panel");
const jobIdDisplay = el("job-id-display");
const statusBadge = el("status-badge");
const proceedBtn = el("proceed-btn");
const stopBtn = el("stop-btn");
const logList = el("log-list");
const versionApprovalPanel = el("version-approval-panel");
const detectedCurrentVersionEl = el("detected-current-version");
const suggestedOutputVersionEl = el("suggested-output-version");
const confirmVersionInput = el("confirm-version-input");
const confirmVersionBtn = el("confirm-version-btn");
const parentVersionField = el("parent-version-field");
const parentTargetVersionInput = el("parent-target-version-input");
const parentVersionHint = el("parent-version-hint");
const artifactsPanel = el("artifacts-panel");
const viewDiffBtn = el("view-diff-btn");
const viewReportBtn = el("view-report-btn");
const viewFilesLink = el("view-files-link");
const handoffList = el("handoff-list");
const artifactViewer = el("artifact-viewer");
const artifactViewerTitle = el("artifact-viewer-title");
const artifactViewerContent = el("artifact-viewer-content");
const copyArtifactBtn = el("copy-artifact-btn");
const downloadArtifactBtn = el("download-artifact-btn");

let currentEventSource = null;

// Injected (not static HTML) so both index.html and job.html get the same
// button + modal for free, same pattern as common.js's injectSettingsUI().
function injectLangGraphHelp() {
  const heading = progressPanel.querySelector("h2");
  const header = document.createElement("div");
  header.className = "panel-header";
  heading.replaceWith(header);
  header.appendChild(heading);

  const helpBtn = document.createElement("button");
  helpBtn.type = "button";
  helpBtn.className = "icon-btn help-btn";
  helpBtn.title = "1단계/2단계가 내부적으로 어떻게 동작하는지 보기";
  helpBtn.textContent = "?";
  header.appendChild(helpBtn);

  const modal = document.createElement("div");
  modal.className = "modal-overlay hidden";
  modal.innerHTML = `
    <div class="modal modal-wide" role="dialog" aria-label="LangGraph 오케스트레이션">
      <h2>LangGraph 오케스트레이션</h2>
      <p class="field-hint">1단계/2단계는 각각 "적용 → 검증 → (실패 시) AI 수정 → 재검증"을 자동으로 반복하는 자가검증 루프입니다. 실선은 항상 지나가는 흐름, 점선은 상태에 따라 갈라지는 분기입니다.</p>

      <h3>Stage 1 — 마이그레이션 스텝 1개</h3>
      <div class="langgraph-diagram">
        <svg viewBox="0 0 900 500" role="img" aria-label="Stage 1 LangGraph: START에서 plan을 거쳐 apply/verify/ai_fix를 오가며 성공 시 END, 재시도 소진 시 handoff를 거쳐 END로 간다">
          <defs>
            <marker id="lg1-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)" />
            </marker>
          </defs>

          <g fill="none" stroke="var(--text-muted)" stroke-width="1.5" marker-end="url(#lg1-arrow)">
            <line x1="115" y1="140" x2="173" y2="140" />
            <line x1="287" y1="140" x2="343" y2="140" stroke-dasharray="4 3" />
            <path d="M230,162 C 300,220 420,260 478,296" stroke-dasharray="4 3" />
            <path d="M230,118 C 230,20 830,20 830,122" stroke-dasharray="4 3" />
            <line x1="455" y1="140" x2="543" y2="140" stroke-dasharray="4 3" />
            <path d="M400,162 C 430,220 460,260 486,296" stroke-dasharray="4 3" />
            <line x1="657" y1="140" x2="783" y2="140" stroke-dasharray="4 3" />
            <path d="M608,162 C 615,220 590,260 560,296" stroke-dasharray="4 3" />
            <path d="M470,298 C 500,240 550,180 583,164" stroke-dasharray="4 3" />
            <path d="M617,162 C 660,260 670,340 655,416" stroke-dasharray="4 3" />
            <path d="M553,325 C 585,360 595,385 601,415" stroke-dasharray="4 3" />
            <path d="M650,418 C 760,400 830,300 830,158" />
          </g>

          <g font-size="12" fill="var(--text-muted)" text-anchor="middle">
            <g><rect x="180" y="122" width="86" height="16" fill="var(--bg-card)" /><text x="223" y="134">recipe 있음</text></g>
            <g><rect x="300" y="222" width="96" height="16" fill="var(--bg-card)" /><text x="348" y="234">recipe 없음</text></g>
            <g><rect x="465" y="30" width="130" height="16" fill="var(--bg-card)" /><text x="530" y="42">이미 목표 버전(스킵)</text></g>
            <g><rect x="460" y="122" width="76" height="16" fill="var(--bg-card)" /><text x="498" y="134">성공</text></g>
            <g><rect x="368" y="222" width="76" height="16" fill="var(--bg-card)" /><text x="406" y="234">적용 실패</text></g>
            <g><rect x="678" y="122" width="76" height="16" fill="var(--bg-card)" /><text x="716" y="134">성공</text></g>
            <g><rect x="470" y="255" width="104" height="16" fill="var(--bg-card)" /><text x="522" y="267">파일수 이하</text></g>
            <g><rect x="600" y="200" width="90" height="16" fill="var(--bg-card)" /><text x="645" y="212">재시도 가능</text></g>
            <g><rect x="600" y="290" width="104" height="16" fill="var(--bg-card)" /><text x="652" y="302">재시도 소진</text></g>
            <g><rect x="555" y="365" width="90" height="16" fill="var(--bg-card)" /><text x="600" y="377">파일수 초과</text></g>
          </g>

          <g font-size="13" font-weight="600" text-anchor="middle">
            <rect x="25" y="122" width="90" height="36" rx="18" fill="var(--accent)" />
            <text x="70" y="145" fill="var(--accent-contrast)">START</text>

            <rect x="175" y="118" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="230" y="145" fill="var(--text)">plan</text>

            <rect x="345" y="118" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="400" y="145" fill="var(--text)">apply</text>

            <rect x="545" y="118" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="600" y="145" fill="var(--text)">verify</text>

            <rect x="785" y="122" width="90" height="36" rx="18" fill="var(--accent)" />
            <text x="830" y="145" fill="var(--accent-contrast)">END</text>

            <rect x="445" y="298" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="500" y="325" fill="var(--text)">ai_fix</text>

            <rect x="595" y="418" width="110" height="44" rx="8" fill="var(--warning-bg)" stroke="var(--warning)" />
            <text x="650" y="445" fill="var(--warning)">handoff</text>
          </g>
        </svg>
      </div>

      <h3>Stage 2 — CVE 패치 1건</h3>
      <div class="langgraph-diagram">
        <svg viewBox="0 0 900 500" role="img" aria-label="Stage 2 LangGraph: START에서 apply를 거쳐 verify로, 실패 시 ai_fix와 오가다가 재시도 소진 시 handoff를 거쳐 END로 간다">
          <defs>
            <marker id="lg2-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)" />
            </marker>
          </defs>

          <g fill="none" stroke="var(--text-muted)" stroke-width="1.5" marker-end="url(#lg2-arrow)">
            <line x1="115" y1="140" x2="173" y2="140" />
            <line x1="287" y1="140" x2="343" y2="140" />
            <line x1="455" y1="140" x2="543" y2="140" stroke-dasharray="4 3" />
            <path d="M608,162 C 615,220 590,260 560,296" stroke-dasharray="4 3" />
            <path d="M470,298 C 500,240 550,180 583,164" stroke-dasharray="4 3" />
            <path d="M617,162 C 660,260 670,340 655,416" stroke-dasharray="4 3" />
            <path d="M553,325 C 585,360 595,385 601,415" stroke-dasharray="4 3" />
            <path d="M650,418 C 760,400 830,300 830,158" />
          </g>

          <g font-size="12" fill="var(--text-muted)" text-anchor="middle">
            <g><rect x="656" y="122" width="120" height="16" fill="var(--bg-card)" /><text x="716" y="134">mvn verify 성공</text></g>
            <g><rect x="470" y="255" width="104" height="16" fill="var(--bg-card)" /><text x="522" y="267">파일수 이하</text></g>
            <g><rect x="600" y="200" width="90" height="16" fill="var(--bg-card)" /><text x="645" y="212">재시도 가능</text></g>
            <g><rect x="600" y="290" width="104" height="16" fill="var(--bg-card)" /><text x="652" y="302">재시도 소진</text></g>
            <g><rect x="555" y="365" width="90" height="16" fill="var(--bg-card)" /><text x="600" y="377">파일수 초과</text></g>
          </g>

          <g font-size="13" font-weight="600" text-anchor="middle">
            <rect x="25" y="122" width="90" height="36" rx="18" fill="var(--accent)" />
            <text x="70" y="145" fill="var(--accent-contrast)">START</text>

            <rect x="175" y="118" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="230" y="145" fill="var(--text)">apply</text>

            <rect x="345" y="118" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="400" y="145" fill="var(--text)">verify</text>

            <rect x="785" y="122" width="90" height="36" rx="18" fill="var(--accent)" />
            <text x="830" y="145" fill="var(--accent-contrast)">END</text>

            <rect x="445" y="298" width="110" height="44" rx="8" fill="var(--bg-sunken)" stroke="var(--border)" />
            <text x="500" y="325" fill="var(--text)">ai_fix</text>

            <rect x="595" y="418" width="110" height="44" rx="8" fill="var(--warning-bg)" stroke="var(--warning)" />
            <text x="650" y="445" fill="var(--warning)">handoff</text>
          </g>
        </svg>
      </div>

      <div class="modal-close-row">
        <button type="button" id="langgraph-modal-close-btn" class="secondary">닫기</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  helpBtn.addEventListener("click", () => modal.classList.remove("hidden"));
  modal.querySelector("#langgraph-modal-close-btn").addEventListener("click", () => modal.classList.add("hidden"));
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) modal.classList.add("hidden");
  });
}

injectLangGraphHelp();

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

function renderVulnerabilitiesPostStage1(vulnerabilities) {
  renderVulnerabilitiesInto(vulnerabilities, {
    section: vulnPostStage1Section,
    tableBody: vulnPostStage1TableBody,
    emptyMsg: vulnPostStage1Empty,
    countBadge: vulnPostStage1Count,
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

function renderVulnerabilitiesFinal(vulnerabilities) {
  renderVulnerabilitiesInto(vulnerabilities, {
    section: vulnFinalSection,
    tableBody: vulnFinalTableBody,
    emptyMsg: vulnFinalEmpty,
    countBadge: vulnFinalCount,
  });
}

function showVersionApprovalPanel(currentVersion, suggestedVersion, detectedParent) {
  versionApprovalPanel.classList.remove("hidden");
  detectedCurrentVersionEl.textContent = currentVersion ?? "-";
  suggestedOutputVersionEl.textContent = suggestedVersion ?? "-";
  confirmVersionInput.value = suggestedVersion ?? "";
  confirmVersionBtn.disabled = false;

  if (detectedParent) {
    parentVersionField.classList.remove("hidden");
    parentVersionHint.classList.remove("hidden");
    parentVersionHint.textContent =
      `이 프로젝트는 사내 parent POM(${detectedParent.group_id}:${detectedParent.artifact_id}, ` +
      `현재 ${detectedParent.version ?? "-"})에서 스택 버전을 상속받습니다. 이미 목표 스택으로 올라간 ` +
      `새 버전이 있다면 입력하세요 (선택).`;
  } else {
    parentVersionField.classList.add("hidden");
    parentVersionHint.classList.add("hidden");
    parentTargetVersionInput.value = "";
  }
}

function hideVersionApprovalPanel() {
  versionApprovalPanel.classList.add("hidden");
  parentVersionField.classList.add("hidden");
  parentVersionHint.classList.add("hidden");
  parentTargetVersionInput.value = "";
}

confirmVersionBtn.addEventListener("click", async () => {
  const jobId = jobIdDisplay.textContent;
  confirmVersionBtn.disabled = true;
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}/confirm-version`), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({
        output_version: confirmVersionInput.value.trim(),
        parent_target_version: parentTargetVersionInput.value.trim() || null,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    // no reconnect needed -- the already-open SSE connection delivers the
    // next "status" (running) event, which hides this panel (see below).
  } catch (err) {
    appendLog(`버전 확인 실패: ${err.message}`, true);
    confirmVersionBtn.disabled = false;
  }
});

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

  es.addEventListener("vulnerabilities_post_stage1", (ev) => {
    renderVulnerabilitiesPostStage1(JSON.parse(ev.data).vulnerabilities);
  });

  es.addEventListener("vulnerabilities", (ev) => {
    renderVulnerabilities(JSON.parse(ev.data).vulnerabilities);
  });

  es.addEventListener("vulnerabilities_final", (ev) => {
    renderVulnerabilitiesFinal(JSON.parse(ev.data).vulnerabilities);
  });

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    setStatusBadge(data.status);
    if (data.error) {
      appendLog(`오류: ${data.error}`, true);
    }
    if (data.status === "awaiting_version_approval") {
      showVersionApprovalPanel(data.current_version, data.suggested_version, data.detected_parent);
    } else {
      hideVersionApprovalPanel();
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

  if (body.diff) {
    viewFilesLink.href = `files.html?job=${encodeURIComponent(jobId)}`;
    viewFilesLink.classList.remove("hidden");
  } else {
    viewFilesLink.classList.add("hidden");
  }

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
