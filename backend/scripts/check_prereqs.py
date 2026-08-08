#!/usr/bin/env python3
"""Standalone prereq checker -- runnable before ``pip install`` since it only
touches stdlib + app/prereqs.py (which itself has zero third-party deps).
Prints a pass/fail table for: java, mvn, git, python, trivy.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows consoles often default to a non-UTF-8 codepage (e.g. cp949), which
# mangles the Korean text below. Force UTF-8 output where supported.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.prereqs import check_all


def main() -> int:
    results = check_all()
    width = max(len(r.name) for r in results)
    print("사전 준비 확인 (README: 로컬 실행 사전 준비)\n")
    all_ok = True
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        print(f"[{mark}] {r.name:<{width}}  ({r.command})")
        print(f"       {r.detail}")
        all_ok = all_ok and r.ok
    print()
    if all_ok:
        print("모든 사전 준비가 완료되었습니다.")
        return 0
    print("일부 항목이 준비되지 않았습니다 -- 위 안내를 따라 설치/설정 후 다시 실행하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
