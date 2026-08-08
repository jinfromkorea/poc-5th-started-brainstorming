# 출력 아티팩트 버전 설정 시 자기참조 BOM 프로퍼티 동기화

## 배경 및 목적

`versioning/artifact_version.apply_output_version`은 `mvn versions:set`으로 프로젝트의 출력 아티팩트 버전을 바꾼다. 리액터(멀티모듈) 프로젝트에서 하위 모듈이 자기 `<version>`을 따로 안 두고 부모 POM의 버전을 상속하는 경우, `versions:set`이 리액터 전체에 새 버전을 정상적으로 전파한다 — 여기까지는 문제없다.

문제는 부모 POM의 `dependencyManagement`가 **그 리액터 자신의 모듈들을 BOM/라이브러리처럼 참조**하면서, 그 참조 버전을 프로퍼티(`${ace.version}` 등)로 선언해둔 경우다. `mvn versions:set`은 이런 프로퍼티까지는 갱신하지 않는다 — 그 결과 모듈의 **실제 빌드 버전**과 그 모듈을 가리키는 **BOM 참조 버전**이 서로 어긋나게 남는다.

**실제 사례** (job #11, `ace-parent`): 하위 모듈 `ace-common`/`ace-ai`/`ace-util`은 부모의 `<version>`을 상속한다. 출력 버전을 `1.0.0`으로 설정하면 이 모듈들의 실제 빌드 버전은 정상적으로 `1.0.0`이 된다. 그런데 부모 POM의 `dependencyManagement`는 이 세 모듈을 `<version>${ace.version}</version>`(값: `0.4.5`, 원본 프로젝트에서는 `<version>`과 우연히 같은 값이었을 뿐 서로 다른 필드다)으로 참조하고 있어, `mvn versions:set` 실행 후에도 `ace.version`은 그대로 `0.4.5`로 남는다 — 이 리액터 안에서 더 이상 만들어지지도 않는 옛 버전을 가리키게 되는 것.

## 범위

- 포함: `apply_output_version`이 `mvn versions:set` 다음에, 위 패턴에 해당하는 프로퍼티를 찾아 `mvn versions:set-property`로 같이 갱신.
- 포함: `mvnrewrite/mvn_client.py`에 `versions:set-property`용 얇은 wrapper 추가.
- 범위 밖: 이 동기화 과정을 사람이 보는 진행 로그(SSE `log` 이벤트)에 별도로 노출하는 것 — 지금 `apply_output_version`은 애초에 `on_log` 콜백을 받지 않고, 이번 수정도 그 시그니처를 바꾸지 않는다. git 커밋 메시지와 `output/logs/ingest/*.log`로는 이미 추적 가능하다.
- 범위 밖: `dependency_patch.py`(Stage 2 CVE 패치 경로)의 기존 `versions:set-property` 인라인 호출을 새 wrapper로 리팩터링하는 것 — 이번 수정과 무관한 이미 동작하는 코드라 손대지 않는다.
- 범위 밖: 프로퍼티가 자기참조 용도 외에 다른 의존성과 우연히 같은 이름으로 재사용되는 경우에 대한 방어 — 드물고, 그렇게 짜여진 프로젝트 쪽의 문제로 본다(아래 "알려진 한계" 참고).

## 설계

### 탐지 로직 (`versioning/artifact_version.py`에 신설)

리액터 루트 `work_dir/pom.xml`을 lxml로 파싱해서(기존 `dependency_patch.find_version_property`, `pom_parser.py`와 같은 방식):

1. `_project_group_id(root)`: `<project><groupId>`가 있으면 그 값, 없으면 `<project><parent><groupId>`, 둘 다 없으면 `None`.
2. `_self_referencing_version_properties(pom_path)`: `group_id`가 `None`이면 빈 집합. 아니면 `<dependencyManagement><dependencies><dependency>`를 순회하며, `groupId`가 프로젝트 자신의 groupId와 같고 `<version>`이 `${프로퍼티}` 형태인 항목들의 프로퍼티 이름을 집합으로 모아 반환(중복 제거 — 같은 프로퍼티를 여러 모듈이 같이 쓰는 게 일반적).

이 로직은 이미 있는 `mvn versions:set`의 `updateMatchingVersions`(리터럴 버전이 옛 프로젝트 버전과 문자열로 일치하면 갱신)로는 못 잡는, **프로퍼티 간접 참조**만을 겨냥한다 — 리터럴 버전 케이스는 이미 `versions:set`이 처리해준다는 게 `dependency_patch.py`의 기존 주석("Maven Versions Plugin의 `versions:use-dep-version`이 `${property}` 참조 의존성은 조용히 건너뛴다")과 대칭되는, 확인된 동작이다.

`<dependencyManagement>`만 훑고 평범한 `<dependencies>`는 보지 않는다(참고로 `find_version_property`는 둘 다 본다) — "자기 리액터 모듈을 BOM/라이브러리처럼 참조"하는 패턴은 정의상 dependencyManagement의 몫이고(부모가 packaging=pom이라 자기 자신에 대한 런타임 `<dependencies>`를 가질 이유가 없다), 실제로 확인된 사례(ace-parent)도 여기 있다.

### `mvn_client.py`에 `mvn_versions_set_property` 추가

```python
async def mvn_versions_set_property(
    work_dir: Path,
    property_name: str,
    new_version: str,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    return await run_subprocess(
        [*_BATCH, "versions:set-property", f"-Dproperty={property_name}", f"-DnewVersion={new_version}"],
        work_dir, settings, log_path=log_path, on_line=on_line,
    )
```

`mvn_versions_set`과 같은 파일, 같은 스타일 — "출력 아티팩트 버전 설정"이라는 같은 개념적 작업의 일부이므로(Stage 2가 쓰는 `dependency_patch.py`의 인라인 호출과는 별개로 둔다, 위 범위 참고).

### `apply_output_version` 변경

```python
async def apply_output_version(work_dir, new_version, settings, output_dir=None) -> str:
    log_path = build_log_path(output_dir, "ingest", "mvn-versions-set") if output_dir is not None else None
    result = await mvn_versions_set(work_dir, new_version, settings, log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"versions:set failed for {new_version!r}: {result.output}")

    for prop in sorted(_self_referencing_version_properties(work_dir / "pom.xml")):
        prop_log_path = (
            build_log_path(output_dir, "ingest", f"mvn-versions-set-property-{prop}") if output_dir is not None else None
        )
        prop_result = await mvn_versions_set_property(work_dir, prop, new_version, settings, log_path=prop_log_path)
        if prop_result.returncode != 0:
            raise RuntimeError(f"versions:set-property failed for property {prop!r}: {prop_result.output}")

    return commit_checkpoint(work_dir, settings, f"checkpoint: set artifact version to {new_version}")
```

- `sorted()`로 순회 순서를 결정적으로 고정 — 로그/테스트 재현성.
- 프로퍼티마다 별도 로그 파일(`mvn-versions-set-property-{prop}.log`) — 기존 "서브프로세스 호출 하나당 로그 파일 하나" 컨벤션(`subprocess_runner.build_log_path`) 그대로.
- 발견된 프로퍼티가 없으면(가장 흔한 경우 — 대부분 프로젝트는 이런 자기참조 BOM 패턴을 안 씀) 추가 mvn 호출 자체가 없다 — 기존 동작과 완전히 동일.
- 커밋은 지금처럼 한 번만(`checkpoint: set artifact version to {new_version}`) — 버전 설정이라는 하나의 논리적 작업이므로 프로퍼티 동기화를 별도 커밋으로 쪼개지 않는다.

## 에러 처리

`versions:set-property` 실패 시 기존 `versions:set` 실패와 동일하게 `RuntimeError`로 job을 실패 처리한다 — 버전이 일부만 반영된 애매한 상태(모듈은 새 버전으로 빌드되는데 BOM 참조는 옛 버전을 가리키는 상태)로 다음 단계(1단계 마이그레이션)에 넘어가지 않게 하기 위함.

## 알려진 한계

자기참조 프로퍼티가 리액터 밖의 무관한 의존성과 이름이 우연히 겹치는 경우(예: `ace.version`을 다른 서드파티 라이브러리 버전에도 재사용) 이 로직은 구분하지 못하고 같이 바꿔버린다. 실제로 이런 식으로 프로퍼티를 재사용하는 건 나쁜 관례이고 참고 저장소 4개 어디에도 없는 패턴이라, 지금은 방어 코드를 넣지 않는다(YAGNI) — 실제로 문제되는 사례가 나오면 그때 프로퍼티 이름 자체에 대한 추가 검증을 고려한다.

## 테스트 계획

**단위** (`backend/tests/unit/test_artifact_version.py`, 신규 — 지금까지 이 모듈 전용 테스트가 없었다):
- `_project_group_id`: 직접 선언된 `<groupId>`, `<parent><groupId>`로만 있는 경우, 둘 다 없는 경우.
- `_self_referencing_version_properties`: ace-parent 구조를 흉내낸 임시 pom.xml(자기참조 BOM 항목 여러 개 + 무관한 서드파티 항목 + 리터럴 버전 항목 섞어서)로 정확히 자기참조 프로퍼티 집합만 반환하는지, 자기참조가 없으면 빈 집합인지.
- `apply_output_version` (mvn 호출은 monkeypatch로 대체, `test_pipeline.py`와 같은 패턴): 자기참조 프로퍼티가 없을 때 `mvn_versions_set_property`가 전혀 호출 안 되는지; 여러 개일 때 정렬된 순서로 각각 호출되는지; 프로퍼티 갱신 실패 시 `RuntimeError`가 나는지(그리고 그 뒤 프로퍼티는 시도 안 하는지).

**통합** (`backend/tests/integration/test_artifact_version.py`, 신규, `slow` 마커, 실제 `data/ace-parent.zip` 참고 저장소 사용 — `test_mvn_client.py`와 같은 패턴):
- `apply_output_version(work_dir, "1.0.0", settings)` 실행 후, 루트 `pom.xml`의 `<version>`과 `<ace.version>` 프로퍼티가 **둘 다** `1.0.0`으로 바뀌었는지 실측 확인 — 지금까지 `test_mvn_client.py::test_versions_set_updates_artifact_version_across_reactor`는 `<version>`만 확인하고 있었는데, 이번 버그의 핵심이 `<ace.version>` 쪽이므로 이 통합 테스트가 실질적인 회귀 방지 역할을 한다.
