"use strict";

const jobNotFound = el("job-not-found");

async function loadJob() {
  const jobId = new URLSearchParams(location.search).get("job");
  if (!jobId) {
    jobNotFound.classList.remove("hidden");
    return;
  }

  const res = await fetch(apiUrl(`/jobs/${encodeURIComponent(jobId)}`), { headers: authHeaders() });
  if (!res.ok) {
    jobNotFound.classList.remove("hidden");
    return;
  }

  progressPanel.classList.remove("hidden");
  jobIdDisplay.textContent = jobId;
  connectSSE(jobId);
}

loadJob();
