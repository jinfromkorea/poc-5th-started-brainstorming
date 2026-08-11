"""1회성: needs_handoff -> stage1_needs_handoff/stage2_needs_handoff 백필
(spec: docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md).

JOB_STATUSES에서 "needs_handoff"가 제거되고 두 값으로 나뉜 뒤, 그 전에 이미
"needs_handoff"로 저장된 기존 job 행을 한 번 옮겨준다. output/handoff/ 안에
stage2-*-guide.md가 있으면 stage2_needs_handoff, 없고 stage1-guide.md만
있으면 stage1_needs_handoff로 판정한다.

실행 (backend/ 에서):
    .venv312/Scripts/python.exe scripts/backfill_needs_handoff_status.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"
JOBS_DIR = Path(__file__).parent.parent / "data" / "jobs"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id FROM jobs WHERE status = 'needs_handoff'").fetchall()
    if not rows:
        print("백필 대상 없음.")
        return

    for (job_id,) in rows:
        handoff_dir = JOBS_DIR / job_id / "output" / "handoff"
        has_stage2 = any(handoff_dir.glob("stage2-*-guide.md"))
        new_status = "stage2_needs_handoff" if has_stage2 else "stage1_needs_handoff"
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
        print(f"job {job_id}: needs_handoff -> {new_status}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
