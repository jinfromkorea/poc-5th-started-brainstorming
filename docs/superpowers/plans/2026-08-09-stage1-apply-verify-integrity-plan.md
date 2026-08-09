# 구현 계획 — Stage 1 apply/verify 정합성 강화

스펙: [`docs/superpowers/specs/2026-08-09-stage1-apply-verify-integrity-design.md`](../specs/2026-08-09-stage1-apply-verify-integrity-design.md)

`writing-plans` 스킬이 이 환경에 없어(이전 두 번의 작업에서 이미 확인, 사용자가 "직접 작성"을 선택) 이 문서도 스펙을 바탕으로 직접 작성했다. 단계는 의존성 순서(mvn_client → state → graph_stage1 → tools → 기존 테스트 이름 갱신 → 신규 테스트 → 문서)를 따른다.

## 0. 사전 확인

- `git status`가 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` 로 기존 테스트가 전부 통과하는 베이스라인 확인.

## 1. `mvnrewrite/mvn_client.py` — `mvn_test_compile` 추가

- 기존 `mvn_compile` 바로 아래, 같은 스타일로 추가(스펙 §설계 1 코드 그대로). `mvn_compile`은 그대로 둔다.

**검증**: `python -c "from app.mvnrewrite.mvn_client import mvn_test_compile"`.

## 2. `orchestration/state.py` — `Stage1State`에 필드 추가

- `apply_returncode: int | None` 추가.
- `graph_stage1.py`의 `initial_state`/`initial_state_for_step` 양쪽 다 `apply_returncode=None`으로 초기화(빠뜨리면 `apply_node`가 실행되기 전에 `route_after_apply`가 절대 안 불리므로 실제로 문제되진 않지만, TypedDict 필드는 항상 전체를 채우는 이 코드베이스의 기존 관례를 따른다).

**검증**: 3단계에서 실제로 채워지는지 같이 확인.

## 3. `orchestration/graph_stage1.py` — 노드/라우팅 변경

- import에 `mvn_test_compile` 추가(`mvn_compile` import는 제거 — 더 이상 이 파일에서 안 씀).
- `apply_node`: 반환 dict에 `"apply_returncode": result.returncode` 추가.
- `verify_node`: `mvn_compile(...)` 호출을 `mvn_test_compile(...)`로 교체. 로그 메시지("컴파일 검증: ...")는 그대로 둬도 됨(테스트 컴파일도 넓은 의미의 "컴파일 검증"이라 스텝 설명과 충돌 없음).
- 새 라우팅 함수 `route_after_apply`(스펙 §설계 3 코드 그대로) 추가.
- 그래프 배선: `graph.add_edge("apply", "verify")` 삭제, `graph.add_conditional_edges("apply", route_after_apply, {"verify": "verify", "ai_fix": "ai_fix"})`로 교체.

**검증**: 5, 6단계 테스트로.

## 4. `orchestration/tools.py` — `run_build` 툴 맞춤

- import를 `mvn_compile` → `mvn_test_compile`로, `run_build` 내부 호출도 동일하게 교체. 툴의 docstring("Run \`mvn compile\`...")도 "Run \`mvn test-compile\`..."로 갱신.

**검증**: `grep -rn "mvn_compile" backend/app`로 더 이상 애플리케이션 코드에서 참조가 없는지 확인(정의부인 `mvn_client.py` 제외).

## 5. 기존 테스트의 monkeypatch 대상 이름 갱신

`grep -rn "graph_stage1.mvn_compile" backend/tests`로 정확한 위치를 다시 확인한 뒤, 각각 `graph_stage1.mvn_test_compile`로 바꾼다(동작 로직은 그대로, 대상 이름만):
- `tests/unit/test_graph_stage1_plan.py` (3곳)
- `tests/unit/test_graph_stage1_retry_loop.py` (4곳)
- `tests/unit/test_multi_step.py` (4곳)

이 과정에서 각 몽키패치가 대체하는 가짜 함수 이름(`fake_mvn_compile`, `counting_mvn_compile` 등) 자체는 안 바꿔도 무방(로컬 변수명이라 무해) — 굳이 일관성 위해 바꾸고 싶다면 바꿔도 되지만 필수는 아님.

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/unit/test_graph_stage1_plan.py tests/unit/test_graph_stage1_retry_loop.py tests/unit/test_multi_step.py -q --basetemp=/c/pytesttmp` — 이름만 바꿨으니 전부 그대로 통과해야 함(새로 깨지는 게 있으면 로직이 아니라 이름 매칭을 놓친 것).

## 6. 신규 단위 테스트 (`test_graph_stage1_retry_loop.py`에 추가)

기존 파일의 monkeypatch 스타일(`run_openrewrite_recipes`, `mvn_test_compile`를 각각 `app.orchestration.graph_stage1.run_openrewrite_recipes`/`mvn_test_compile`로 패치)을 따라 스펙 §테스트 계획의 3가지 케이스를 추가:

- 레시피 실패(exit=1) → `verify`가 호출되지 않고 곧장 `ai_fix`로 가는지(`verify` 자리에 카운터 심은 가짜 함수를 넣어 0번 호출 확인).
- 레시피 실패 시 `ai_fix`가 받는 `HumanMessage`에 레시피 실패 출력(`last_build_output`)이 그대로 포함되는지 — `create_agent`/`ChatOpenAI`까지 실제로 부를 수는 없으니, 기존 파일이 이미 하고 있는 방식대로 `app.orchestration.graph_stage1.create_agent`(또는 유사 지점)를 몽키패치해서 넘어온 프롬프트 내용을 캡처.
- 레시피 성공(exit=0) → 기존처럼 `verify`가 정상 호출되는지(회귀 확인, 라우팅이 성공 케이스는 안 건드렸는지).

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/unit/test_graph_stage1_retry_loop.py -q --basetemp=/c/pytesttmp`

## 7. 문서 갱신

- `docs/architecture.md` §7.2: mermaid 다이어그램의 `apply --> verify: OpenRewrite 레시피 실행` 실선을, `apply --> verify`(성공, 실선 유지)와 `apply --> ai_fix`(레시피 실패, 점선 추가) 두 갈래로 갱신. 아래 설명 문단의 `verify: mvn compile`도 `mvn test-compile`로.
- `docs/langgraph-orchestration.md` §3.2 Stage 1 다이어그램에도 `apply -.->|레시피 실패(exit≠0)| ai_fix` 점선 추가, §3.3 노드별 설명 표의 `apply`/`verify` 행을 갱신.

**검증**: mermaid 블록의 따옴표/화살표 문법이 깨지지 않았는지 육안 확인(이 프로젝트 문서에서 반복적으로 있었던 실수 패턴).

## 8. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` — 0단계 베이스라인과 비교해 새로 깨진 테스트 없는지, 신규 테스트 포함 전부 통과하는지.
- (선택, 시간 되면) job #11 재현: `ace-parent.zip`으로 실제 job을 Boot 3.5→4.1 + Spring AI 2.0까지 다시 돌려서, 이번엔 Spring AI 스텝이 `ai_fix`로 들어가는지, 그리고 그 스텝이 "완료"로 끝난다면 실제로 `git diff`에 변경 파일이 있는지(0개인데 완료로 잘못 보고되는 예전 버그가 재현 안 되는지) 확인.
