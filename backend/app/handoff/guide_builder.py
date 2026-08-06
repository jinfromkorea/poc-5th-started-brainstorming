"""Builds the "AI 인수인계 가이드" (spec: "AI 인수인계 가이드 (실패 시)") -- a
Markdown user-prompt a human can paste straight into another AI coding tool
(Copilot, Codex, Claude Desktop, ...) to keep going where this tool got
stuck. Built by template from state already on hand (the failing step, the
final build output, and the ai_fix conversation trace) rather than another
LLM call -- everything the spec's 5 sections need is already present in
that state, and a template keeps this deterministic and free to generate.

Shared by both Stage 1 (a migration step) and Stage 2 (a CVE patch) --
neither PlanStep nor Vulnerability is passed in directly; callers pass the
plain description/mechanism-used strings so this module doesn't need to
depend on either stage's domain types.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage


def _summarize_attempts(messages: list) -> str:
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                lines.append(f"- 시도: `{call['name']}({call.get('args', {})})`")
        elif isinstance(msg, ToolMessage):
            excerpt = str(msg.content)[:300].replace("\n", " ")
            lines.append(f"  → 결과: {excerpt}")
    return "\n".join(lines) if lines else "(AI가 별도로 시도한 코드 수정 기록 없음)"


def build_handoff_guide(
    description: str,
    mechanism_used: str | None,
    messages: list,
    last_build_output: str,
    target_summary: str,
) -> str:
    attempted = _summarize_attempts(messages)
    build_output = last_build_output[-4000:]

    return f"""# AI 인수인계 가이드: {description}

다른 AI 코딩 도구(GitHub Copilot, Codex, Claude Desktop 등)에 아래 내용을 그대로 붙여넣어 이어서 진행할 수 있습니다.

## 1. 마이그레이션 맥락
- 현재 시도 중인 작업: {description}
- 최종 목표 스택: {target_summary}
- 사용한 자동화 수단: {mechanism_used or "(알려진 자동 수단 없음 -- 직접 코드 수정 필요)"}

## 2. 여기까지 성공적으로 적용된 변경
이 단계는 검증(빌드)을 통과하지 못해 롤백되었습니다. 이 단계 자체의 변경은 현재 작업 디렉토리에 반영되어 있지 않지만, 이전 단계까지는 정상적으로 체크포인트 커밋되어 있습니다.

## 3. 실패한 에러 메시지
```
{build_output}
```

## 4. 이미 시도했지만 실패한 방법
{attempted}

## 5. 다음에 확인/수정해야 할 것
위 에러 메시지와 시도 기록을 참고해서, `{description}` 작업을 계속 진행하기 위한 코드 수정을 제안해 주세요. 이미 시도했지만 실패한 방법은 반복하지 마세요.
"""
