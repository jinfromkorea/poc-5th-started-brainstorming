"use strict";

const jobForm = el("job-form");
const submitBtn = el("submit-btn");
const formError = el("form-error");
const gitFields = el("git-fields");
const zipFields = el("zip-fields");
const gitUrlInput = el("git-url");
const gitRefInput = el("git-ref");
const zipFileInput = el("zip-file");
const runStage1Checkbox = el("run-stage1");
const runStage2Checkbox = el("run-stage2");

document.querySelectorAll('input[name="source-type"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const isGit = document.querySelector('input[name="source-type"]:checked').value === "git";
    gitFields.classList.toggle("hidden", !isGit);
    zipFields.classList.toggle("hidden", isGit);
  });
});

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
    // Progress/analysis/artifacts live only on job.html now (index.html no
    // longer carries that markup or job-view.js) -- hand off there instead
    // of driving SSE inline.
    location.href = `job.html?job=${encodeURIComponent(body.job_id)}`;
  } catch (err) {
    formError.textContent = `작업 시작 실패: ${err.message}`;
    formError.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "작업 시작";
  }
});
