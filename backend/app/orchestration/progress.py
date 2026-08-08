"""Shared type + no-op default for the optional on_log progress callback
threaded through Stage 1/2's orchestration functions (multi_step.py,
stage2_loop.py, graph_stage1.py, graph_stage2.py), so pipeline.py can stream
fine-grained progress (per-step, per-CVE, per-AI-fix-attempt) to the job's
SSE log instead of only coarse start/end messages. Defaulting to a no-op
keeps every function callable without a callback (tests, ad-hoc scripts)
rather than forcing None-checks at every call site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

LogFn = Callable[[str], Awaitable[None]]


async def noop_log(message: str) -> None:
    pass
