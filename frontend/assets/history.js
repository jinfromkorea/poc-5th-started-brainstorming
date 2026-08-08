"use strict";

const jobsError = el("jobs-error");
const jobsTableBody = el("jobs-table-body");
const refreshJobsBtn = el("refresh-jobs-btn");

function formatDateTime(iso) {
  return new Date(iso).toLocaleString();
}

async function loadJobs() {
  jobsError.classList.add("hidden");
  const res = await fetch(apiUrl("/jobs"), { headers: authHeaders() });
  if (!res.ok) {
    jobsError.textContent = `작업 목록을 불러오지 못했습니다 (HTTP ${res.status})`;
    jobsError.classList.remove("hidden");
    return;
  }
  const jobs = await res.json();

  jobsTableBody.innerHTML = "";
  jobs.forEach((job) => {
    const row = document.createElement("tr");

    const idCell = document.createElement("td");
    const idLink = document.createElement("a");
    idLink.href = `job.html?job=${encodeURIComponent(job.job_id)}`;
    idLink.textContent = job.job_id;
    idCell.appendChild(idLink);

    const statusCell = document.createElement("td");
    const statusSpan = document.createElement("span");
    statusSpan.className = `badge status-${job.status}`;
    statusSpan.textContent = job.status;
    statusCell.appendChild(statusSpan);

    row.appendChild(idCell);
    row.appendChild(statusCell);
    [
      job.source_ref,
      job.output_version || "-",
      job.run_stage1 ? "O" : "-",
      job.run_stage2 ? "O" : "-",
      formatDateTime(job.created_at),
      formatDateTime(job.updated_at),
    ].forEach((text) => {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.appendChild(cell);
    });

    jobsTableBody.appendChild(row);
  });
}

refreshJobsBtn.addEventListener("click", loadJobs);

loadJobs();
