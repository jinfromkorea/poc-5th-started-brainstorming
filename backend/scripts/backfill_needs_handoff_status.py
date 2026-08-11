"""1회성: needs_handoff -> stage1_needs_handoff/stage2_needs_handoff 백필
(spec: docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md).

JOB_STATUSES에서 "needs_handoff"가 제거되고 두 값으로 나뉜 뒤, 그 전에 이미
"needs_handoff"로 저장된 기존 job을 옮겨준다. output/handoff/ 안에
stage2-*-guide.md가 있으면 stage2_needs_handoff, 없고 stage1-guide.md만
있으면 stage1_needs_handoff로 판정한다.

두 곳을 모두 고친다 -- 처음 버전은 jobs.status만 고치고 job_events는
안 건드려서, SSE 재생 시 마지막 "status" 이벤트가 여전히 옛값("needs_handoff")
그대로 프론트엔드로 전달돼 재개 버튼이 안 뜨는 문제가 있었다(job_events가
jobs.status의 소스가 아니라 그 반대라, 이 스크립트가 jobs 행을 바꿔도
job_events에 이미 영속된 과거 이벤트 JSON은 저절로 안 바뀐다):

1. `jobs.status`
2. `job_events`에서 `event_type='status'`이고 JSON의 `status` 필드가
   "needs_handoff"인 모든 행(보통 job당 마지막 status 이벤트 하나) -- SSE가
   재접속 시 재생하는 게 이 테이블이므로, 여기를 안 고치면 화면에는 여전히
   옛 상태가 표시된다.

재실행해도 안전하다 -- 이미 옮겨진 job은 jobs.status가 더 이상
"needs_handoff"가 아니므로 판정 대상에서 자연히 빠지지만, job_events 쪽은
jobs.status와 무관하게 직접 스캔하므로 job_events만 덜 고쳐진 상태였어도
다시 실행하면 마저 고쳐진다.

실행 (backend/ 에서):
    .venv312/Scripts/python.exe scripts/backfill_needs_handoff_status.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"
JOBS_DIR = Path(__file__).parent.parent / "data" / "jobs"


def _determine_new_status(job_id: str) -> str:
    handoff_dir = JOBS_DIR / job_id / "output" / "handoff"
    has_stage2 = any(handoff_dir.glob("stage2-*-guide.md"))
    return "stage2_needs_handoff" if has_stage2 else "stage1_needs_handoff"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    # job_events가 실제로 화면에 재생되는 원천이므로, jobs.status가 아니라
    # 여기서 직접 대상 job_id를 찾는다 (jobs.status는 이미 고쳐졌는데
    # job_events만 안 고쳐진 경우도 다시 잡아내기 위함).
    event_rows = conn.execute("SELECT id, job_id, data FROM job_events WHERE event_type = 'status'").fetchall()
    affected_job_ids: set[str] = set()
    for _id, job_id, data in event_rows:
        if json.loads(data).get("status") == "needs_handoff":
            affected_job_ids.add(job_id)

    if not affected_job_ids:
        print("백필 대상 없음.")
        conn.close()
        return

    for job_id in sorted(affected_job_ids):
        new_status = _determine_new_status(job_id)

        conn.execute("UPDATE jobs SET status = ? WHERE id = ? AND status = 'needs_handoff'", (new_status, job_id))

        updated_events = 0
        for event_id, _job_id, data in event_rows:
            if _job_id != job_id:
                continue
            parsed = json.loads(data)
            if parsed.get("status") != "needs_handoff":
                continue
            parsed["status"] = new_status
            conn.execute("UPDATE job_events SET data = ? WHERE id = ?", (json.dumps(parsed), event_id))
            updated_events += 1

        print(f"job {job_id}: needs_handoff -> {new_status} (job_events {updated_events}건 포함)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
