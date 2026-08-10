"use strict";

const jobsError = el("jobs-error");
const jobsTableBody = el("jobs-table-body");
const refreshJobsBtn = el("refresh-jobs-btn");

const ACTIVE_STATUSES = new Set(["queued", "running", "awaiting_approval"]);

async function stopJob(jobId, btn) {
  if (!confirm("정말 이 작업을 중지할까요? 지금까지의 변경 내용은 저장되지 않습니다.")) return;
  btn.disabled = true;
  btn.textContent = "중지 중...";
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}/cancel`), { method: "POST", headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`중지 요청 실패: ${err.message}`);
  } finally {
    loadJobs();
  }
}

async function deleteJob(jobId, btn) {
  if (!confirm("이 작업 이력을 삭제할까요? 관련 파일도 함께 삭제되며 복구할 수 없습니다.")) return;
  btn.disabled = true;
  btn.textContent = "삭제 중...";
  try {
    const res = await fetch(apiUrl(`/jobs/${jobId}`), { method: "DELETE", headers: authHeaders() });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  } finally {
    loadJobs();
  }
}

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

    const actionCell = document.createElement("td");
    actionCell.className = "row-actions";

    const detailLink = document.createElement("a");
    detailLink.href = `job.html?job=${encodeURIComponent(job.job_id)}`;
    detailLink.textContent = "상세";
    actionCell.appendChild(detailLink);

    const filesLink = document.createElement("a");
    filesLink.href = `files.html?job=${encodeURIComponent(job.job_id)}`;
    filesLink.textContent = "파일";
    actionCell.appendChild(filesLink);

    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "secondary";
    if (ACTIVE_STATUSES.has(job.status)) {
      actionBtn.textContent = "중지";
      actionBtn.addEventListener("click", () => stopJob(job.job_id, actionBtn));
    } else {
      actionBtn.textContent = "삭제";
      actionBtn.addEventListener("click", () => deleteJob(job.job_id, actionBtn));
    }
    actionCell.appendChild(actionBtn);
    row.appendChild(actionCell);

    jobsTableBody.appendChild(row);
  });
}

async function refreshJobs() {
  refreshJobsBtn.disabled = true;
  refreshJobsBtn.classList.add("spinning");
  try {
    await loadJobs();
  } finally {
    refreshJobsBtn.disabled = false;
    refreshJobsBtn.classList.remove("spinning");
  }
}

refreshJobsBtn.addEventListener("click", refreshJobs);

loadJobs();
