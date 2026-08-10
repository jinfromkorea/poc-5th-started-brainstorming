"use strict";

const STORAGE_KEYS = { apiBase: "maven-stack-upgrade-tool.apiBase", apiToken: "maven-stack-upgrade-tool.apiToken" };
const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const el = (id) => document.getElementById(id);

function injectSettingsUI() {
  const settingsBtn = document.createElement("button");
  settingsBtn.type = "button";
  settingsBtn.id = "settings-btn";
  settingsBtn.className = "settings-btn";
  settingsBtn.title = "설정";
  settingsBtn.textContent = "⚙";
  document.querySelector("header").appendChild(settingsBtn);

  const overlay = document.createElement("div");
  overlay.id = "settings-modal";
  overlay.className = "modal-overlay hidden";
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-label="설정">
      <h2>설정</h2>
      <div class="field-row">
        <label for="api-base">API 서버 주소</label>
        <input id="api-base" type="text" placeholder="http://127.0.0.1:8010" />
      </div>
      <div class="field-row">
        <label for="api-token">API 토큰 (X-API-Token, 설정된 경우만)</label>
        <input id="api-token" type="password" placeholder="비어있으면 인증 없이 요청" />
      </div>
      <h3 class="modal-section-heading">취약점 DB 캐시</h3>
      <div class="field-row">
        <span id="cache-status-text">불러오는 중...</span>
        <button type="button" id="cache-refresh-btn" class="secondary icon-btn" title="지금 갱신">⟳</button>
      </div>
      <h3 class="modal-section-heading">LLM 모델</h3>
      <div class="field-row">
        <label for="llm-model-select">사용할 모델</label>
        <select id="llm-model-select"></select>
      </div>
      <div class="modal-close-row">
        <button type="button" id="settings-close-btn" class="secondary">닫기</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  settingsBtn.addEventListener("click", () => {
    overlay.classList.remove("hidden");
    loadCacheStatus();
    loadLlmModel();
  });
  el("settings-close-btn").addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.classList.add("hidden");
  });
  el("cache-refresh-btn").addEventListener("click", startCacheRefresh);
  el("llm-model-select").addEventListener("change", onLlmModelChange);
}

injectSettingsUI();

const apiBaseInput = el("api-base");
const apiTokenInput = el("api-token");

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

loadConnectionSettings();

const cacheStatusText = el("cache-status-text");
const cacheRefreshBtn = el("cache-refresh-btn");
let cacheRefreshEventSource = null;

const llmModelSelect = el("llm-model-select");

function formatCacheTimestamp(iso) {
  return iso ? new Date(iso).toLocaleString() : "갱신 기록 없음";
}

function setCacheSpinning(spinning) {
  cacheRefreshBtn.disabled = spinning;
  cacheRefreshBtn.classList.toggle("spinning", spinning);
}

// Steady-state view: one line each for NVD/Trivy (+ an optional error line),
// instead of a single "NVD: ... · Trivy: ..." line. Transient states (log
// lines while refreshing, error messages) just use cacheStatusText.textContent
// directly, which replaces this multi-line markup until the next call here.
function renderCacheStatusLines(nvdText, trivyText, errorText) {
  cacheStatusText.innerHTML = "";
  const nvdLine = document.createElement("div");
  nvdLine.textContent = `NVD: ${nvdText}`;
  const trivyLine = document.createElement("div");
  trivyLine.textContent = `Trivy: ${trivyText}`;
  cacheStatusText.append(nvdLine, trivyLine);
  if (errorText) {
    const errorLine = document.createElement("div");
    errorLine.className = "error";
    errorLine.textContent = errorText;
    cacheStatusText.appendChild(errorLine);
  }
}

async function loadCacheStatus() {
  try {
    const res = await fetch(apiUrl("/cache/status"), { headers: authHeaders() });
    if (!res.ok) {
      cacheStatusText.textContent = `캐시 상태를 불러오지 못했습니다 (HTTP ${res.status})`;
      return;
    }
    const body = await res.json();

    if (body.refreshing) {
      setCacheSpinning(true);
      cacheStatusText.textContent = "갱신 중...";
      connectCacheRefreshSSE(body.current_job_id);
      return;
    }

    setCacheSpinning(false);
    const errorText =
      body.last_run_status === "failed" ? `마지막 갱신 실패: ${body.last_run_error || "알 수 없는 오류"}` : null;
    renderCacheStatusLines(formatCacheTimestamp(body.nvd_last_updated_at), formatCacheTimestamp(body.trivy_last_updated_at), errorText);
  } catch (err) {
    cacheStatusText.textContent = `캐시 상태를 불러오지 못했습니다: ${err.message}`;
  }
}

async function startCacheRefresh() {
  setCacheSpinning(true);
  cacheStatusText.textContent = "갱신 시작 중...";
  try {
    const res = await fetch(apiUrl("/cache/refresh"), { method: "POST", headers: authHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const body = await res.json();
    connectCacheRefreshSSE(body.job_id);
  } catch (err) {
    setCacheSpinning(false);
    cacheStatusText.textContent = `갱신 요청 실패: ${err.message}`;
  }
}

function connectCacheRefreshSSE(jobId) {
  if (cacheRefreshEventSource) {
    cacheRefreshEventSource.close();
  }
  const token = getApiToken();
  const query = token ? `?api_token=${encodeURIComponent(token)}` : "";
  const es = new EventSource(apiUrl(`/jobs/${jobId}/events${query}`));
  cacheRefreshEventSource = es;

  es.addEventListener("log", (ev) => {
    const data = JSON.parse(ev.data);
    cacheStatusText.textContent = data.message;
  });

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    if (data.status === "success" || data.status === "failed") {
      es.close();
      loadCacheStatus();
    }
  });
}

async function loadLlmModel() {
  try {
    const res = await fetch(apiUrl("/settings/llm-model"), { headers: authHeaders() });
    if (!res.ok) return;
    const body = await res.json();
    llmModelSelect.innerHTML = "";
    body.available.forEach((model) => {
      const opt = document.createElement("option");
      opt.value = model;
      opt.textContent = model;
      if (model === body.current) opt.selected = true;
      llmModelSelect.appendChild(opt);
    });
  } catch (err) {
    // Fails quietly -- the select just stays empty; the rest of the modal
    // (connection settings, cache status) still works.
  }
}

async function onLlmModelChange() {
  const chosen = llmModelSelect.value;
  try {
    const res = await fetch(apiUrl("/settings/llm-model"), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ model: chosen }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`모델 변경 실패: ${err.message}`);
    loadLlmModel(); // revert the select back to the actual current value
  }
}
