"use strict";

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
const checkVersionBtn = el("check-version-btn");
const versionHint = el("version-hint");

document.querySelectorAll('input[name="source-type"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const isGit = document.querySelector('input[name="source-type"]:checked').value === "git";
    gitFields.classList.toggle("hidden", !isGit);
    zipFields.classList.toggle("hidden", isGit);
  });
});

async function peekArtifactVersion() {
  const sourceType = document.querySelector('input[name="source-type"]:checked').value;
  const fd = new FormData();
  if (sourceType === "git") {
    if (!gitUrlInput.value.trim()) return;
    fd.append("git_url", gitUrlInput.value.trim());
    if (gitRefInput.value.trim()) fd.append("git_ref", gitRefInput.value.trim());
  } else {
    if (!zipFileInput.files[0]) return;
    fd.append("zip_file", zipFileInput.files[0]);
  }

  versionHint.textContent = "현재 버전 확인 중...";
  versionHint.classList.remove("hidden");
  checkVersionBtn.disabled = true;
  try {
    const res = await fetch(apiUrl("/inspect/artifact-version"), { method: "POST", headers: authHeaders(), body: fd });
    const body = await res.json();
    if (!res.ok || !body.detected_version) {
      versionHint.textContent = "현재 버전을 확인하지 못했습니다.";
      return;
    }
    const sourceNote = body.source === "parent.version" ? " (parent에서 상속)" : "";
    versionHint.textContent = `감지된 현재 버전: ${body.detected_version}${sourceNote}`;
    if (!outputVersionInput.value.trim() && body.suggested_version) {
      outputVersionInput.value = body.suggested_version;
    }
  } catch (err) {
    versionHint.textContent = "현재 버전을 확인하지 못했습니다.";
  } finally {
    checkVersionBtn.disabled = false;
  }
}

checkVersionBtn.addEventListener("click", peekArtifactVersion);
zipFileInput.addEventListener("change", peekArtifactVersion);

function resetProgressUI(jobId) {
  progressPanel.classList.remove("hidden");
  jobIdDisplay.textContent = jobId;
  setStatusBadge("queued");
  logList.innerHTML = "";
  proceedBtn.classList.add("hidden");
  artifactsPanel.classList.add("hidden");
  artifactViewer.classList.add("hidden");

  analysisPanel.classList.add("hidden");
  stackInfo.classList.add("hidden");
  vulnBaselineSection.classList.add("hidden");
  vulnBaselineTableBody.innerHTML = "";
  vulnBaselineEmpty.classList.add("hidden");
  vulnSection.classList.add("hidden");
  vulnTableBody.innerHTML = "";
  vulnEmpty.classList.add("hidden");
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
