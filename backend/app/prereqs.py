"""Local-execution prerequisite checks (spec: "README: 로컬 실행 사전 준비").

Shared by the /prereqs API endpoint and scripts/check_prereqs.py so there is
exactly one place that knows how to invoke each tool and what to say when
it's missing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class PrereqResult:
    name: str
    command: str
    ok: bool
    detail: str
    install_hint: str


def _run_version(binary: str, args: list[str]) -> tuple[bool, str]:
    exe = shutil.which(binary)
    if exe is None:
        return False, f"'{binary}' not found on PATH"
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",  # not locale.getpreferredencoding() (cp949 on Korean
            errors="replace",  # Windows) -- see checkpoint/git_repo.py's _run_git for why
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - report any failure as a failed check
        return False, f"failed to run '{binary} {' '.join(args)}': {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    first_line = output.strip().splitlines()[0] if output.strip() else "(no output)"
    ok = proc.returncode == 0
    return ok, first_line


CHECKS = [
    {
        "name": "Java",
        "binary": "java",
        "args": ["-version"],
        "used_for": "대상 프로젝트 빌드/검증(mvn compile/test/verify) 및 최종 목표(Java 21)용 JDK",
        "install_hint": "https://adoptium.net (또는 사내 표준 JDK 배포 경로)에서 Java 21 이상 설치",
    },
    {
        "name": "Maven",
        "binary": "mvn",
        "args": ["-version"],
        "used_for": "대상 프로젝트의 Maven 빌드, OpenRewrite(mvn rewrite:run), Maven Versions Plugin 실행",
        "install_hint": "https://maven.apache.org/download.cgi 에서 설치, PATH에 bin/ 추가",
    },
    {
        "name": "Git",
        "binary": "git",
        "args": ["--version"],
        "used_for": "Git URL 인입(git clone), work/ 디렉토리의 체크포인트/롤백(git init/commit/reset) — ZIP 인입이어도 항상 필요",
        "install_hint": "https://git-scm.com/downloads 에서 설치",
    },
    {
        "name": "Python",
        "binary": "python",
        "args": ["--version"],
        "used_for": "이 도구 자신(FastAPI 백엔드)의 실행",
        "install_hint": "https://www.python.org/downloads/ 에서 3.12 설치",
    },
    {
        "name": "Trivy",
        "binary": "trivy",
        "args": ["--version"],
        "used_for": "2단계 취약점 스캔",
        "install_hint": "https://aquasecurity.github.io/trivy/latest/getting-started/installation/",
    },
]


def check_all() -> list[PrereqResult]:
    results = []
    for c in CHECKS:
        ok, detail = _run_version(c["binary"], c["args"])
        results.append(
            PrereqResult(
                name=c["name"],
                command=f"{c['binary']} {' '.join(c['args'])}",
                ok=ok,
                detail=detail if ok else f"{detail} — 용도: {c['used_for']} — 설치: {c['install_hint']}",
                install_hint=c["install_hint"],
            )
        )
    return results
