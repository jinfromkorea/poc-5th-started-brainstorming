"""Final diff/patch (spec: "산출물 및 안전장치" -- diff/패치, 사람이 검토 후
수동 반영, 자동 커밋/푸시 없음). Because only verified checkpoint commits
ever land on work/'s HEAD (failed attempts get git-reset away), a plain
baseline..HEAD diff is automatically "verified changes only" with no
separate filtering needed.
"""

from __future__ import annotations

from pathlib import Path

from app.checkpoint.git_repo import diff_since
from app.config import Settings


def write_diff(work_dir: Path, settings: Settings, baseline_sha: str, output_path: Path) -> Path:
    diff_text = diff_since(work_dir, settings, baseline_sha)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diff_text, encoding="utf-8")
    return output_path
