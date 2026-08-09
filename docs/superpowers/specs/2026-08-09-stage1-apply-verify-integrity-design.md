# Stage 1 apply/verify 정합성 강화 — 레시피 실패가 조용히 "성공"으로 보고되는 문제

## 배경 및 목적

job #11에서 `[3/3] Spring AI 1.1.8 -> 2.0 (서드파티 레시피)` 스텝이 로그상 "완료, 체크포인트 저장"으로 끝났지만, 실제로는 **OpenRewrite 레시피가 단 한 줄도 코드를 바꾸지 못했다**(레시피 적용 자체가 exit=1로 실패). `git show`로 직접 확인한 결과 `checkpoint: applied recipe io.arconia.rewrite.spring.ai2.UpgradeSpringAi_2_0` 커밋의 변경 파일 수는 0개다.

### 근본 원인 (두 가지가 겹쳐서 발생)

1. **`verify_node`가 운영 소스만 본다**: `graph_stage1.py`의 `verify_node`는 `mvn compile`(운영 소스만 컴파일)만 실행한다. job #11에서 실제로 깨진 건 `ace-common/src/test/java/.../EmailUtilTest.java`(테스트 소스) — Boot 3.5→4.0 레시피(`UpgradeSpringBoot_4_0`)가 지나가면서 `org.springframework.boot.autoconfigure.mail.MailSenderAutoConfiguration`(Spring Boot 4.0에서 재구성된 mail 자동설정 패키지)을 못 옮겨준 채로 남긴 것. `mvn compile`은 테스트 소스를 아예 안 보므로 1·2번 스텝의 자가검증을 그냥 통과해버렸다.
2. **`apply` → `verify`가 조건 없는 고정 엣지다**: OpenRewrite의 `rewrite:run`은 리액터 전체가 `process-test-classes`(테스트 소스 컴파일 포함) 단계를 통과해야 실제로 레시피를 적용한다. 위 1번 문제로 `ace-common`의 테스트 컴파일이 깨져있었던 탓에 `mvn rewrite:run` 자체가 `BUILD FAILURE`(exit=1)로 끝났고 — 레시피는 적용도 못 해봤다. 그런데 `graph_stage1.build_stage1_graph`의 `apply` 노드는 이 실패 여부와 무관하게 무조건 `verify`로 넘어가고, `verify`는 (1번 문제로) 운영 소스 컴파일만 확인해 통과해버려 전체 그래프가 `status=success`로 끝났다.

이 두 문제는 **서로 다른 실패 모드**를 각각 커버한다 — 하나만 고치면 안 된다:
- 1번만 고치면: 레시피가 아무것도 못 바꿨는데(exit≠0) 운영+테스트 소스가 우연히 멀쩡하게 컴파일되는 경우(레시피가 그냥 조용히 no-op으로 실패)를 여전히 놓친다.
- 2번만 고치면: 레시피 자체는 exit=0으로 "성공"했지만 그 결과로 테스트 코드만 깨지는 경우(운영 컴파일은 여전히 통과)를 여전히 놓친다.

## 범위

- 포함: `orchestration/graph_stage1.py`(Stage 1 자가검증 루프) — `verify_node`의 검증 범위 확장, `apply` 노드의 실패를 그래프 라우팅에 반영.
- 포함: `mvnrewrite/mvn_client.py`에 `mvn_test_compile` 추가, `orchestration/tools.py`의 `run_build` 툴도 동일하게 맞춤(AI 자신이 보는 빌드 결과와 `verify_node`가 보는 결과가 어긋나지 않도록).
- 포함: `docs/architecture.md` §7.2, `docs/langgraph-orchestration.md`의 Stage 1 다이어그램/설명 갱신.
- 범위 밖: `orchestration/graph_stage2.py`(2단계 CVE 패치)도 `apply` → `verify`가 동일하게 고정 엣지인, 같은 계열의 문제를 갖고 있다 — 이번엔 의도적으로 손대지 않는다(Stage 2의 `verify_node`는 이미 `mvn verify`를 써서 검증 범위 자체는 충분하고, 라우팅 문제만 남아있음. 확인은 됐으니 필요하면 별도로 다룬다).
- 범위 밖: `mvn test`/`mvn verify`(테스트 **실행**까지)로 강화하는 것 — 이 저장소의 참고 프로젝트(`ace-parent`) 안에는 실제 메일 전송을 시도하는 테스트(`EmailUtilTest.send()`)가 있어 자동 실행 시 부작용 위험이 있다. 컴파일 여부만 확인하는 `mvn test-compile`로 충분히 이번 문제(테스트 소스의 import 깨짐)를 잡을 수 있다.

## 설계

### 1. `mvnrewrite/mvn_client.py` — `mvn_test_compile` 추가

```python
async def mvn_test_compile(
    work_dir: Path, settings: Settings, log_path: Path | None = None, on_line: Callable[[str], None] | None = None
) -> SubprocessResult:
    return await run_subprocess([*_BATCH, "test-compile"], work_dir, settings, log_path=log_path, on_line=on_line)
```

`mvn_compile`은 그대로 둔다(삭제 안 함) — `mvn_test`도 지금 애플리케이션 코드 어디서도 안 쓰이면서 남아있는 것과 같은 기존 관례이고(`grep` 확인됨), `test_mvn_client.py`의 기존 테스트(`test_mvn_compile_succeeds_on_reference_repo`)도 계속 유효하다.

### 2. `orchestration/state.py` — `Stage1State`에 필드 추가

```python
apply_returncode: int | None
```

`apply` 노드가 방금 돌린 레시피의 종료 코드를 기록한다. `initial_state`/`initial_state_for_step`은 `apply_returncode=None`으로 시작한다(레시피가 아직 한 번도 안 돌았음을 뜻함 — `recipe=None`인 스텝은 애초에 `apply` 노드 자체를 안 거치므로 계속 `None`으로 남아도 무방).

### 3. `orchestration/graph_stage1.py` — 노드/라우팅 변경

- `apply_node`: 반환 dict에 `"apply_returncode": result.returncode` 추가.
- `verify_node`: `mvn_compile` → `mvn_test_compile`로 교체(import도 함께).
- 새 라우팅 함수:
  ```python
  def route_after_apply(state: Stage1State) -> str:
      return "verify" if state["apply_returncode"] == 0 else "ai_fix"
  ```
- 그래프 배선: `graph.add_edge("apply", "verify")`를 제거하고
  ```python
  graph.add_conditional_edges("apply", route_after_apply, {"verify": "verify", "ai_fix": "ai_fix"})
  ```
  로 교체.

레시피 실패로 `ai_fix`에 곧장 들어가는 경우, `state["recipe"]`는 `None`이 아니므로(레시피가 없는 스텝은 애초에 `apply`를 거치지 않는다 — `route_after_plan`이 곧장 `ai_fix`로 보냄) `ai_fix_node`의 기존 분기 로직이 코드 변경 없이 그대로 맞는 프롬프트("레시피 적용 후 빌드 실패 — 출력: ...")를 만든다. `last_build_output`에는 이미 `[openrewrite exit=1]\n<mvn rewrite:run 실패 로그>`가 담겨 있어, `verify`를 거쳤다면 그 결과로 덮어써져 사라졌을 진짜 실패 원인을 AI에게 그대로 전달할 수 있다.

### 4. `orchestration/tools.py` — `run_build` 툴 맞춤

`ai_fix_node`가 AI에게 주는 `run_build` 툴(`mvn_compile` 호출)도 `mvn_test_compile`로 바꾼다. 안 바꾸면 AI는 자기 툴로는 "빌드 통과"라고 보는데 그래프의 `verify_node`는 (다음 라운드에서) 여전히 테스트 컴파일 실패로 보는 엇박이 생겨 재시도가 낭비된다.

## 문서 갱신

- `docs/architecture.md` §7.2: `apply --> verify: OpenRewrite 레시피 실행` 고정 화살표를 조건부(레시피 실패 시 `ai_fix`로)로 갱신, `verify` 설명을 `mvn compile` → `mvn test-compile`로.
- `docs/langgraph-orchestration.md` §3.2 Stage 1 다이어그램(및 §3.3 노드 설명)도 동일하게 갱신 — `apply` 노드에서 나가는 화살표가 이제 실선 하나가 아니라 점선(조건부) 하나 추가.

## 테스트 계획

**기존 테스트 영향** (동작은 그대로, monkeypatch 대상 이름만 바뀜 — `mvn_compile` → `mvn_test_compile`, `grep`으로 확인된 10곳):
- `tests/unit/test_graph_stage1_plan.py` (3곳)
- `tests/unit/test_graph_stage1_retry_loop.py` (4곳)
- `tests/unit/test_multi_step.py` (4곳)

**신규 단위 테스트** (`test_graph_stage1_retry_loop.py`에 추가, 기존 스타일 따름):
- 레시피 자체가 실패(`run_openrewrite_recipes`를 exit=1로 monkeypatch)하면, `verify`가 몽키패치된 채로도(성공하도록 해놔도) 호출되지 않고 곧장 `ai_fix`로 가는지 — `verify` 몽키패치에 카운터를 심어 0번 호출됨을 확인.
- 레시피가 exit=1로 실패했을 때 `ai_fix`가 받는 프롬프트(`HumanMessage`)에 레시피 실패 출력이 포함되는지(`verify`가 개입해 값을 덮어쓰지 않았는지 확인하는 회귀 테스트).
- `verify_node`가 `mvn_test_compile`을 호출하는지(함수 이름 자체가 바뀌었으므로 기존 "compile 성공/실패" 테스트들이 새 이름으로도 여전히 같은 의미로 동작하는지).

**job #11 재현** (선택, 시간 되면): job #11과 같은 시나리오(ace-parent, Boot 3.5→4.1→Spring AI 2.0)를 실제로 다시 돌려서, 이번엔 Spring AI 스텝이 `ai_fix`로 들어가 `EmailUtilTest.java`의 깨진 import를 스스로 고치려 시도하는지(성공하든 `needs_handoff`로 끝나든, 최소한 "레시피 0줄 변경인데 성공"으로 잘못 보고되지는 않는지) 확인.
