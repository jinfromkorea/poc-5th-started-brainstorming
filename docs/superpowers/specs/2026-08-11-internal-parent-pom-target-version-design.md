# 사내 parent POM(BOM 겸용) 목표 버전 전이

## 배경 및 목적

job #35/#38(`anne-agent`, `com.poscodx.ai.ace:ace-parent`를 상속)을 조사하는 과정에서 확인된 구조: `anne-agent`는 자기 `pom.xml`에 `java.version`/`spring-boot.version`/`spring-ai.version` 같은 스택 프로퍼티를 전혀 선언하지 않고, 전부 사내 parent POM인 `ace-parent`(BOM 겸용 — `<dependencyManagement>`로 버전 관리 + `<build><pluginManagement>`로 빌드 설정까지 겸함, `docs/superpowers/specs/` 다른 문서 참고할 필요 없이 이 프로젝트 자체가 실측 사례)에서 상속받는다. `ace-parent`는 별도로 릴리스되는 아티팩트라 이 도구가 인입하는 소스(git/zip) 범위 밖에 있다.

Stage 1의 마이그레이션 계획(`orchestration/planning.build_migration_plan`)과 그 실행(OpenRewrite 레시피, AI 직접 수정)은 인입된 `work/` 트리 안의 파일만 건드릴 수 있다. 스택 프로퍼티가 전부 parent 상속이면 `anne-agent` 자신의 `pom.xml`엔 바꿀 게 없어서, 1단계가 겉보기엔 진행되는 것처럼 보여도 실제로는 목표 스택에 도달하지 못한다.

이 문서는 이 문제를 "이미 목표 스택으로 올라간 parent POM의 새 버전이 사내 Nexus에 이미 배포돼 있다"고 가정하고, 그 버전으로 `<parent><version>`을 교체하는 방식으로 해결한다. parent 저장소 자체를 함께 마이그레이션하는 것은 범위 밖이다(사용자 확인 완료 — 이미 준비된 새 parent 버전으로 "갈아타는" 시나리오만 다룸).

## 범위

- 백엔드: 사내 parent 감지(`ingest/maven_detect.py`), 기존 Stage 0 `awaiting_version_approval` 게이트에 감지 결과 통합, `mvnrewrite/parent_patch.py`(신규, parent 버전 교체), `orchestration/graph_stage1.py`의 자가검증 루프에 `parent_pom` 스텝 종류 추가, `orchestration/multi_step.py`가 parent 스텝 성공 후 스택을 재분석해 나머지 계획을 다시 세움.
- 프론트엔드: 버전 확인 패널에 조건부로 나타나는 "사내 parent POM 목표 버전" 입력 필드.
- 범위 밖:
  - 사내 parent 저장소를 별도로 ingest해서 함께 마이그레이션하는 것 (사용자 확인: "새 parent 버전으로 교체" 방식을 선택함).
  - 조부모(parent의 parent) 등 다단 상속 체인 지원.
  - "사내 parent" 여부를 완벽하게 판별하는 정교한 로직 — 공개 parent 허용목록 기반의 보수적 휴리스틱(모르면 일단 물어본다)으로 충분하다고 판단.
  - Spring Cloud/AI 등 개별 프로퍼티 단위로 "이건 로컬 선언, 이건 상속"을 구분하는 것 — `<parent>`가 있고 허용목록 밖이면 감지된 스택 전체가 영향받을 수 있다고 보수적으로 취급.

## 결정 사항

- **감지 시점/방법**: Stage 0가 `mvn effective-pom` 분석을 마친 직후, 인입된 프로젝트의 **루트 원본** `pom.xml`(`work/pom.xml`, effective 아님)에 `<parent>`가 있는지 확인한다. 있고 그 `groupId:artifactId`가 알려진 공개 parent 허용목록(`org.springframework.boot:spring-boot-starter-parent` 등 소수)에 없으면 "사내 parent POM 가능성 있음"으로 판단한다.
  - 루트 pom.xml의 `<parent>`는 정의상 인입된 소스 밖의 아티팩트를 가리킨다(멀티모듈 자식이 리액터 루트를 `<parent>`로 삼는 것과는 다른 케이스 — 그건 `<modules>`로 이미 인입된 트리 안에 있어서 지금도 문제없이 처리됨).
- **HITL 게이트는 새로 안 만들고 기존 `awaiting_version_approval`에 통합한다.** Stage 0가 멈추는 시점이 정확히 이 정보를 알게 되는 시점과 같기 때문. 새 job 상태를 추가하지 않는다.
- **입력**: `POST /jobs/{id}/confirm-version` 요청에 선택 필드 `parent_target_version`을 추가한다. 비우면 지금처럼 동작한다(이 프로젝트 파일만 대상, parent는 안 건드림 — 감지된 스택 프로퍼티가 전부 parent 상속이면 사실상 대부분 "변경 없음"으로 끝나고, 리포트에 안내 문구가 남는다).
- **확인값이 감지된 현재 parent 버전과 같으면 409.** 출력 버전의 "동일 버전 확인 불가" 정책과 동일한 이유 — 새 parent 버전은 항상 플랫폼 팀이 이미 릴리스해둔, 지금과는 다른 버전이어야 의미가 있다.
- **적용 메커니즘은 `mvn versions:update-parent`가 아니라 pom.xml `<parent><version>` 직접 교체(lxml).** `versions:update-parent -DparentVersion=X`를 실측해보니 `X`를 정확히 고정(pin)하지 않고 버전 범위/메타데이터 기준으로 "그보다 크거나 같은 것 중 있는 걸" 골라버리는 걸 확인했다(로컬에 `0.5.0`을 타깃으로 줬는데 로컬 저장소에 있던 `4.1`로 가버림 — 사내 Nexus에도 다른 팀이 올린 더 "숫자가 큰" 버전이 있으면 같은 문제가 재현될 수 있음). 사람이 명시적으로 확인한 정확한 값을 그대로 박아 넣는 게 맞으므로, `<parent>`의 `<version>` 텍스트만 정확히 치환한다. `groupId`/`artifactId`는 건드리지 않는다.
- **검증/재시도는 Stage 1의 기존 자가검증 루프(graph_stage1)를 그대로 재사용한다** — 새 상태 머신을 만들지 않는다. `apply` 노드가 OpenRewrite 레시피 대신 parent 버전 교체를 수행하는 새 분기를 갖고, `verify`(`mvn test-compile`)/`ai_fix`/`handoff`는 기존 로직 그대로 재사용. 새 parent가 실제로 코드 비호환(deprecated API 등)을 일으키면 AI 수정 재시도가 그대로 적용된다 — parent 버전 교체 자체가 실패하는 경우(예: 존재하지 않는 버전)와, 교체는 됐지만 컴파일이 깨지는 경우를 굳이 다르게 처리할 이유가 없다.
- **parent 스텝이 성공하면, 그 시점 `work/`를 기준으로 `mvn effective-pom`을 다시 돌리고 스택을 재감지해서 나머지(Java/Boot/Cloud/AI) 계획을 다시 세운다.** Stage 0 시점에 감지한 낡은 버전 기준으로 이미 계산된 계획을 그대로 밀어붙이면, 새 parent가 이미 목표에 도달해 있는 부분까지 불필요하게(또는 잘못) 재시도하게 된다.
- **parent 스텝은 항상 계획의 맨 앞**에 온다(Java 스텝보다도 먼저) — parent가 바뀌면 감지되는 Java 버전조차 달라질 수 있으므로, 그 뒤에 이어지는 모든 계산은 parent 교체 이후 상태를 기준으로 다시 이뤄져야 한다.

## 백엔드 설계

### `ingest/maven_detect.py` — 사내 parent 감지

```python
_PUBLIC_PARENT_ALLOWLIST = {
    ("org.springframework.boot", "spring-boot-starter-parent"),
}


@dataclass
class ExternalParentInfo:
    group_id: str
    artifact_id: str
    version: str | None  # <parent>에 <version> 텍스트가 비어 있는(malformed pom.xml) 방어적 케이스만 None


def detect_external_parent(root_pom: Path) -> ExternalParentInfo | None:
    """Stage 0가 mvn effective-pom 분석 직후 호출. root_pom은 원본(비-effective)
    pom.xml -- 루트 프로젝트의 <parent>는 정의상 인입된 소스 밖(별도로 릴리스되는
    아티팩트)을 가리키므로, 공개 parent 허용목록에 없으면 "사내 parent POM(BOM
    겸용) 가능성 있음"으로 본다(spec: docs/superpowers/specs/2026-08-11-
    internal-parent-pom-target-version-design.md). 실제 사례: anne-agent가
    ace-parent를 상속(job #35/#38)."""
    root = _parse_pom(root_pom)
    parent_el = root.find("{*}parent")
    if parent_el is None:
        return None
    group_id = _text(parent_el, "groupId")
    artifact_id = _text(parent_el, "artifactId")
    if group_id is None or artifact_id is None:
        return None
    if (group_id, artifact_id) in _PUBLIC_PARENT_ALLOWLIST:
        return None
    return ExternalParentInfo(group_id=group_id, artifact_id=artifact_id, version=_text(parent_el, "version"))
```

### `orchestration/pipeline.py` — Stage 0에 통합

`run_pipeline`의 Stage 0 블록, `mvn_effective_pom`/`extract_versions` 직후:

```python
detected_parent = detect_external_parent(work_dir / "pom.xml")
...
await set_job_status(session_factory, job_id, "awaiting_version_approval")
await emit(
    "status",
    {
        "status": "awaiting_version_approval",
        "current_version": current_version,
        "suggested_version": suggested_version,
        "detected_parent": asdict(detected_parent) if detected_parent else None,
    },
)
```

### `schemas/job.py`

```python
class ConfirmVersionRequest(BaseModel):
    output_version: str
    parent_target_version: str | None = None
```

### `api/routers/jobs.py` — `confirm_version`

기존 "확인값이 현재 버전과 같으면 409" 검사 바로 아래, `parent_target_version`이 주어진 경우에 대해 같은 패턴을 하나 더:

```python
detected_parent = detect_external_parent(settings.jobs_dir / job_id / "work" / "pom.xml")
if body.parent_target_version and detected_parent and body.parent_target_version == detected_parent.version:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"parent target version must differ from the current parent version ({detected_parent.version})",
    )
...
manager.start(
    job_id,
    lambda: run_pipeline_resume_after_version_confirm(
        job_id, body.output_version, settings, factory, parent_target_version=body.parent_target_version
    ),
)
```

### `mvnrewrite/parent_patch.py` — 신규, parent 버전 교체 (mechanical)

```python
"""Bumps a project's own <parent><version> to a specific, already-released
target version (Stage 1's optional "사내 parent POM 목표 버전" step). Confirmed
empirically (against a real ace-parent/anne-agent project) that `mvn
versions:update-parent -DparentVersion=X` does NOT reliably pin to exactly
X -- it resolves against version metadata and can silently jump to a
different, numerically "higher" version available locally/remotely instead
of the one actually requested. A direct, exact XML edit has no such
ambiguity -- the <parent>'s groupId/artifactId are left untouched, only
<version> changes."""

from __future__ import annotations
from pathlib import Path
from lxml import etree


def patch_parent_version(pom_path: Path, new_version: str) -> None:
    tree = etree.parse(str(pom_path))
    root = tree.getroot()
    parent_el = root.find("{*}parent")
    if parent_el is None:
        raise ValueError(f"{pom_path}: no <parent> element to update")
    version_el = parent_el.find("{*}version")
    if version_el is None:
        raise ValueError(f"{pom_path}: <parent> has no <version> to update")
    version_el.text = new_version
    tree.write(str(pom_path), xml_declaration=True, encoding="UTF-8")
```

### `orchestration/planning.py` — 새 `StepKind`

```python
StepKind = Literal["parent_pom", "java", "spring_boot", "spring_ai"]
```

`build_migration_plan` 자체는 안 건드린다 — parent 스텝은 카탈로그 조회가 필요 없는 독립적인 스텝이라, `multi_step.run_stage1_migration`이 계획 맨 앞에 직접 붙인다(아래).

### `orchestration/state.py` — `Stage1State`에 필드 추가

```python
class Stage1State(TypedDict):
    ...
    step_kind: StepKind  # "parent_pom"이면 apply_node/ai_fix_node가 다른 경로를 탐
```

### `orchestration/graph_stage1.py` — 5곳 수정

`initial_state_for_step`에 `step_kind=step.kind` 추가. 나머지:

```python
def route_after_plan(state: Stage1State) -> str:
    if state.get("status") == "success":
        return END
    if state.get("step_kind") == "parent_pom":
        return "apply"  # recipe가 None이어도 ai_fix로 새지 않고 mechanical apply로
    return "ai_fix" if state.get("recipe") is None else "apply"


async def apply_node(state: Stage1State) -> dict:
    work_dir = Path(state["work_dir"])
    output_dir = work_dir.parent / "output"
    if state.get("step_kind") == "parent_pom":
        target = state["target_spring_boot"]  # 기존 관례대로 "이 스텝의 target_version" 범용 슬롯으로 재사용
        log_path = build_log_path(output_dir, "stage1", "parent-pom-update")
        started_at = time.monotonic()
        try:
            patch_parent_version(work_dir / "pom.xml", target)
            returncode, output = 0, f"parent <version> set to {target}"
        except Exception as exc:  # noqa: BLE001 -- surfaced via last_build_output, same as a failed subprocess
            returncode, output = 1, str(exc)
        elapsed = time.monotonic() - started_at
        commit_checkpoint(work_dir, settings, f"checkpoint: 사내 parent POM 버전을 {target}로 교체")
        await on_log(f"  parent POM 버전 교체 {'완료' if returncode == 0 else '실패'} ({elapsed:.1f}s)")
        return {"apply_returncode": returncode, "last_build_output": f"[parent-patch exit={returncode}]\n{output}"}
    # ... 기존 OpenRewrite 레시피 분기 그대로 ...


async def ai_fix_node(state: Stage1State) -> dict:
    ...
    if state.get("step_kind") == "parent_pom":
        instruction = (
            f"Updating this project's <parent><version> to {state['target_spring_boot']} is not compiling. "
            f"Build output (may be truncated):\n{state['last_build_output'][-6000:]}"
        )
    elif state["recipe"] is None and state["attempt"] == 0:
        ...  # 기존 분기들 그대로
```

(`route_after_apply`/`verify_node`/`route_after_verify`/`route_after_ai_fix`/`handoff_node`는 이미 범용적이라 변경 없음 — `apply_returncode`/`last_build_output`/`status`만 보고 판단하기 때문.)

### `orchestration/multi_step.py` — parent 스텝 실행 + 재계획

시그니처는 `parent_target_version: str | None = None` 하나만 늘어난다(기본값 있는 선택 인자라 기존 호출부 전부 그대로 컴파일됨 — `tests/unit/test_multi_step.py`/`tests/integration/test_multi_step_real.py`도 안 고쳐도 됨). `output_dir`은 새 파라미터로 받지 않고, `graph_stage1.apply_node`가 이미 쓰는 것과 같은 관례(`work_dir.parent / "output"`, `ingest/workspace.py`의 `WorkspacePaths` 구조)로 내부에서 그대로 유도한다.

```python
async def run_stage1_migration(
    job_id: str,
    work_dir: Path,
    detected: DetectedVersions,
    baseline_commit: str,
    settings: Settings,
    target_boot: str = "4.1",
    target_java: str = "21",
    target_ai: str = "2.0",
    parent_target_version: str | None = None,  # 신규
    on_log: LogFn = noop_log,
) -> MigrationRunResult:
    output_dir = work_dir.parent / "output"  # graph_stage1.apply_node과 동일한 관례
    outcomes: list[StepOutcome] = []
    all_steps: list[PlanStep] = []

    if parent_target_version:
        parent_step = PlanStep(
            kind="parent_pom",
            description=f"사내 parent POM 버전을 {parent_target_version}로 교체",
            recipe=None,
            artifact=None,
            target_version=parent_target_version,
        )
        all_steps.append(parent_step)
        await on_log(f"[사내 parent POM] {parent_step.description} 시작")
        result_state = await run_stage1_step(job_id, work_dir, parent_step, settings, on_log=on_log)

        if result_state["status"] != "success":
            reset_to_checkpoint(work_dir, settings, current_head(work_dir, settings))
            outcomes.append(StepOutcome(step=parent_step, status="needs_handoff"))
            handoff_guide = build_handoff_guide(
                description=parent_step.description,
                mechanism_used=None,
                messages=result_state.get("messages", []),
                last_build_output=result_state.get("last_build_output", ""),
                target_summary=TARGET_STACK_SUMMARY,
            )
            await on_log("[사내 parent POM] 막힘 — AI 인수인계 가이드 생성됨")
            final_diff = diff_since(work_dir, settings, baseline_commit)
            report = build_report(MigrationPlan(steps=all_steps), outcomes, handoff_guide_path=Path("output/handoff"))
            return MigrationRunResult(
                plan=MigrationPlan(steps=all_steps), outcomes=outcomes, status="needs_handoff",
                final_diff=final_diff, report=report, handoff_guide=handoff_guide,
            )

        commit_checkpoint(work_dir, settings, parent_step.description)
        outcomes.append(StepOutcome(step=parent_step, status="success"))
        await on_log("[사내 parent POM] 완료, 체크포인트 저장 — 스택 재분석 중")

        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            work_dir, effective_pom_path, settings,
            log_path=build_log_path(output_dir, "stage1", "mvn-effective-pom-post-parent"),
        )
        detected = extract_versions(effective_pom_path)
        await on_log(
            f"재분석 결과: Java {detected.java_version} / Spring Boot {detected.spring_boot_version} / "
            f"Spring Cloud {detected.spring_cloud_version} / Spring AI {detected.spring_ai_version}"
        )

    plan = build_migration_plan(detected, target_boot=target_boot, target_java=target_java, target_ai=target_ai)
    all_steps.extend(plan.steps)

    if all_steps:
        numbered = "\n".join(f"  {i}. {step.description}" for i, step in enumerate(all_steps, 1))
        await on_log(f"마이그레이션 계획 수립: 총 {len(all_steps)}단계\n{numbered}")

    handoff_guide: str | None = None
    status: RunStatus = "success" if (parent_target_version or plan.steps) else "no_gap"
    total = len(plan.steps)

    for idx, step in enumerate(plan.steps, 1):
        # ... 기존 루프 그대로 (outcomes.append, commit_checkpoint, handoff_guide 처리) ...

    final_diff = diff_since(work_dir, settings, baseline_commit)
    report = build_report(MigrationPlan(steps=all_steps), outcomes, handoff_guide_path=Path("output/handoff") if handoff_guide else None)

    return MigrationRunResult(
        plan=MigrationPlan(steps=all_steps), outcomes=outcomes, status=status,
        final_diff=final_diff, report=report, handoff_guide=handoff_guide,
    )
```

`status` 계산을 `"no_gap" if not plan.steps else "success"`에서 `"success" if (parent_target_version or plan.steps) else "no_gap"`로 바꾼 이유: parent 교체 자체가 실제로 뭔가를 한 행위이므로, 그 뒤 나머지 계획이 비어 있어도(=parent 교체만으로 목표 도달) "아무것도 안 함"(`no_gap`)이 아니라 "성공"(`success`)이 맞다.

### `orchestration/pipeline.py` — `run_pipeline_resume_after_version_confirm` 호출부 갱신

```python
async def run_pipeline_resume_after_version_confirm(
    job_id: str,
    confirmed_version: str,
    settings: Settings,
    session_factory: sessionmaker[Session],
    parent_target_version: str | None = None,  # 신규
) -> None:
    ...
    if run_stage1:
        stage1_result = await run_stage1_migration(
            job_id, work_dir, detected, baseline, settings,
            parent_target_version=parent_target_version, on_log=log,
        )
```

## 프론트엔드 설계

### `assets/job-view.js`

- `status` 이벤트 핸들러의 `awaiting_version_approval` 분기에서, `data.detected_parent`가 있으면 버전 확인 패널에 다음을 추가로 표시:
  - 안내 문구: `이 프로젝트는 사내 parent POM(${group_id}:${artifact_id}, 현재 ${version})에서 스택 버전을 상속받습니다.`
  - 입력창 `parent-target-version-input` (선택, 비워도 진행 가능).
- `confirmVersionBtn` 클릭 핸들러의 요청 바디에 `parent_target_version: parentTargetVersionInput.value.trim() || null` 추가.
- `data.detected_parent`가 없으면 이 입력창 자체를 숨김(대부분의 job에서는 안 보임).

### `index.html`/`job.html`

`version-approval-panel` 안, 기존 "적용할 출력 버전" 필드 아래에 조건부(`hidden` 기본) 블록 추가:

```html
<div id="parent-version-field" class="field-row hidden">
  <label for="parent-target-version-input">사내 parent POM 목표 버전 (선택)</label>
  <input id="parent-target-version-input" type="text" />
</div>
<p id="parent-version-hint" class="field-hint hidden"></p>
```

## 에러 처리 / 엣지 케이스

- 감지된 `detected_parent`가 있는데 `parent_target_version`을 안 주면: 기존 동작 그대로(이 프로젝트 파일만 대상). 스택 프로퍼티가 전부 parent 상속이라 계획이 비면 `no_gap`으로 끝나던 것과 동일 — 이 경우 사용자가 "왜 아무것도 안 바뀌었지"라고 헷갈리지 않도록, `build_report`가 아니라 `multi_step.run_stage1_migration` 호출부(`pipeline.py`)에서 `detected_parent is not None and not parent_target_version`이면 리포트 끝에 안내 문구를 덧붙인다: `"이 프로젝트의 스택 버전 일부는 사내 parent POM(...)에서 관리됩니다 — 이 프로젝트만으로는 목표에 도달할 수 없습니다. parent를 먼저 올리거나, 이미 올라간 parent의 새 버전을 알고 있다면 다음 실행 시 입력하세요."`
- `parent_target_version`을 줬는데 해당 버전이 Nexus에 실제로 없는 경우: `patch_parent_version`은 텍스트만 바꾸므로 성공하고, 그다음 `verify`(`mvn test-compile`)가 parent를 resolve 못 해 실패 → `attempt` 소진 후 `handoff`로 정상적으로 수렴(존재하지 않는 버전을 AI가 고칠 수는 없으므로 결국 인수인계로 끝나는 게 맞는 동작 — 별도의 "resolve 실패는 즉시 handoff" 특수 처리는 하지 않는다. 재시도 한도(`COMPILE_FIX_MAX_ATTEMPTS`, 기본 2)만큼 AI가 시도하다 실패하는 것으로 충분).
- `parent_target_version` 확인값이 감지된 현재 parent 버전과 같으면 409(위 결정 사항).
- 루트 `pom.xml`에 `<parent>`가 아예 없거나 공개 allowlist 안에 있으면 `detected_parent`는 `None` — 프론트에 필드 자체가 안 뜨고, `parent_target_version`을 보내도(엔드포인트 자체는 막지 않음) `run_stage1_migration`은 `<parent>`가 없는 pom에 `patch_parent_version`을 시도해 `ValueError`를 던지고, 그 스텝은 즉시 `needs_handoff`로 처리한다(정상적인 handoff 경로 — 애초에 잘못 입력한 경우이므로 사람이 인지하게 하는 게 맞음).

## 테스트 계획

**단위**:
- `detect_external_parent`: 공개 parent(허용목록) → `None`. 사내 parent(`ace-parent` 형태) → `ExternalParentInfo` 반환. `<parent>` 자체가 없음 → `None`.
- `patch_parent_version`: `<parent><version>`만 정확히 바뀌고 groupId/artifactId는 그대로인지. `<parent>` 없는 pom → `ValueError`.
- `graph_stage1`의 `route_after_plan`/`apply_node`/`ai_fix_node`가 `step_kind="parent_pom"`일 때 각각 올바른 분기를 타는지(mock으로 `patch_parent_version` 대체).
- `multi_step.run_stage1_migration`: `parent_target_version` 없이 호출 시 기존 동작과 동일(회귀 방지). 있을 때 parent 스텝이 계획 맨 앞에 오고, 성공 시 `mvn_effective_pom`/`extract_versions`가 재호출되는지(mock), 실패 시 나머지 계획을 아예 안 세우고 바로 `needs_handoff`로 끝나는지.

**통합** (실제 `mvn`, `ace-parent.zip`/`anne-agent.zip` 재사용, `.m2`에 두 버전의 `ace-parent`를 미리 `install`해두고 실행):
- `patch_parent_version` + 실제 `mvn test-compile`이 새 parent 버전을 정확히 resolve하는지(임의의 "더 큰 버전"으로 새지 않는지 — 이번 조사에서 실측한 `versions:update-parent`의 실패 사례를 회귀 테스트로 고정).
- `POST /jobs/{id}/confirm-version`에 `parent_target_version` 포함 → Stage 1이 parent 스텝부터 시작해 성공적으로 목표 스택까지 도달하는 end-to-end 플로우(`anne-agent.zip` 사용, 목표 스택이 이미 반영된 가짜 `ace-parent` 새 버전을 로컬에 준비).
