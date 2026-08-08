# 구현 계획 — 출력 아티팩트 버전 설정 시 자기참조 BOM 프로퍼티 동기화

스펙: [`docs/superpowers/specs/2026-08-09-output-version-self-reference-sync-design.md`](../specs/2026-08-09-output-version-self-reference-sync-design.md)

`writing-plans` 스킬이 이 환경에 없어(이전 job-cancellation 작업 때 확인, 사용자가 "직접 작성"을 선택) 이 문서도 브레인스토밍 스펙을 바탕으로 직접 작성했다. 단계는 의존성 순서(mvn_client → artifact_version 탐지/변경 → 단위 테스트 → 통합 테스트 → 전체 검증)를 따른다.

## 0. 사전 확인

- `git status`가 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` 로 기존(느린/외부 제외) 테스트가 전부 통과하는 베이스라인 확인.

## 1. `mvnrewrite/mvn_client.py` — `mvn_versions_set_property` 추가

- 기존 `mvn_versions_set` 바로 아래에, 같은 스타일로 추가:

```python
async def mvn_versions_set_property(
    work_dir: Path,
    property_name: str,
    new_version: str,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """versions:set-property -- 리액터 모듈 자기 자신을 BOM/라이브러리처럼
    참조하는 dependencyManagement 프로퍼티(예: ace.version)처럼,
    versions:set의 리액터 전파가 안 따라가는 프로퍼티 간접 참조 버전을
    직접 갱신한다. apply_output_version이 versions:set 다음에 이 함수를
    필요한 만큼 반복 호출한다(versioning/artifact_version.py)."""
    return await run_subprocess(
        [*_BATCH, "versions:set-property", f"-Dproperty={property_name}", f"-DnewVersion={new_version}"],
        work_dir,
        settings,
        log_path=log_path,
        on_line=on_line,
    )
```

**검증**: import/문법 확인 수준(`python -c "from app.mvnrewrite.mvn_client import mvn_versions_set_property"`). 실제 동작 검증은 4단계 통합 테스트에서.

## 2. `versioning/artifact_version.py` — 탐지 로직 + `apply_output_version` 변경

- 상단에 `import re`, `from lxml import etree` 추가(다른 POM 파싱 모듈과 같은 라이브러리).
- `_PROP_REF_RE = re.compile(r"^\$\{([^}]+)\}$")` (pom_parser.py와 동일 패턴, 이 모듈 전용으로 별도 정의 — 기존 관례상 모듈 간 이런 소규모 중복은 허용됨, dependency_patch.py도 독립적으로 자기 것을 둠).
- `_project_group_id(root: etree._Element) -> str | None`: 스펙 §설계 그대로 — 직접 `<groupId>` 우선, 없으면 `<parent><groupId>`.
- `_self_referencing_version_properties(pom_path: Path) -> set[str]`: 스펙 §설계 그대로 — `_project_group_id`가 `None`이면 빈 집합, 아니면 `dependencyManagement/dependencies`만 순회(평범한 `dependencies`는 안 봄 — 스펙에 근거 명시됨).
- `apply_output_version`을 스펙의 코드 그대로 변경: `mvn_versions_set` 성공 후 `sorted(_self_referencing_version_properties(work_dir / "pom.xml"))`을 순회하며 `mvn_versions_set_property` 호출, 프로퍼티마다 별도 로그 파일(`mvn-versions-set-property-{prop}`), 실패 시 `RuntimeError`. 커밋은 기존처럼 마지막에 한 번만.
- `mvn_client` import에 `mvn_versions_set_property` 추가.

**검증**: 3단계 단위 테스트로.

## 3. `backend/tests/unit/test_artifact_version.py` (신규)

`test_dependency_patch.py`와 같은 스타일(임시 pom.xml을 `tmp_path`에 직접 작성, 실제 mvn 없이 검증):

- `_project_group_id`: 직접 `<groupId>` 있는 경우 / `<parent><groupId>`만 있는 경우 / 둘 다 없는 경우.
- `_self_referencing_version_properties`:
  - ace-parent 축소판(자기 groupId로 `${ace.version}` 참조하는 항목 2~3개 + 무관한 서드파티 항목 + 리터럴 버전 항목 섞기) → `{"ace.version"}`만 반환되는지.
  - 자기참조 항목이 아예 없는 pom → 빈 집합.
  - `<groupId>`도 `<parent>`도 없는 pom → 빈 집합(예외 안 남).
- `apply_output_version` (`app.versioning.artifact_version.mvn_versions_set`/`mvn_versions_set_property`를 monkeypatch, `test_pipeline.py`의 몽키패치 패턴 참고):
  - 자기참조 프로퍼티가 없는 pom.xml → `mvn_versions_set_property`가 전혀 호출되지 않는지(기존 동작 그대로 보존되는지의 회귀 확인).
  - 자기참조 프로퍼티가 여러 개인 pom.xml → 정렬된 순서로 각각 호출되는지(호출 인자 캡처해서 확인).
  - `mvn_versions_set_property`가 실패(returncode != 0)를 반환하도록 몽키패치 → `RuntimeError` 발생, 그 이후 프로퍼티는 시도 안 되는지.
  - (기존에 테스트가 아예 없었으므로) 정상 경로 — `commit_checkpoint`까지 호출되어 반환값이 커밋 sha인지도 같이 확인.

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/unit/test_artifact_version.py -q --basetemp=/c/pytesttmp`

## 4. `backend/tests/integration/test_artifact_version.py` (신규, `slow` 마커)

`test_mvn_client.py`와 같은 패턴(`data/ace-parent.zip` 참고 저장소, 실제 `mvn` 호출):

```python
pytestmark = pytest.mark.slow

async def test_apply_output_version_syncs_self_referencing_bom_property(settings):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)
    # ingest()가 이미 work_dir에 git init + baseline 커밋을 해두므로
    # apply_output_version의 commit_checkpoint가 바로 동작할 수 있는 상태.

    checkpoint_sha = await apply_output_version(result.paths.work, "1.0.0", settings)

    root_pom = (result.paths.work / "pom.xml").read_text(encoding="utf-8")
    assert "<version>1.0.0</version>" in root_pom
    assert "<ace.version>1.0.0</ace.version>" in root_pom  # 이번 수정의 핵심 회귀 확인
    assert checkpoint_sha  # 커밋이 실제로 만들어졌는지
```

기존 `test_mvn_client.py::test_versions_set_updates_artifact_version_across_reactor`는 손대지 않는다(그 테스트는 `mvn_versions_set` 자체를 검증하는 것으로 여전히 유효 — 이번에 새로 만드는 테스트는 `apply_output_version`이라는 한 단계 위 함수를 검증).

**검증**: `backend/.venv312/Scripts/python.exe -m pytest tests/integration/test_artifact_version.py -q -m slow --basetemp=/c/pytesttmp` (기본 `addopts`가 `slow`를 제외하므로 `-m slow`로 명시 실행해야 함).

## 5. 전체 검증

- 기본 스위트(빠른 테스트만): `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp` — 0단계 베이스라인과 비교해 새로 깨진 테스트 없는지.
- `slow` 포함 전체(실제 mvn 필요, 시간 걸림): `backend/.venv312/Scripts/python.exe -m pytest -q -m "slow or not slow" --basetemp=/c/pytesttmp` 또는 `--override-ini="addopts="`로 기본 제외를 풀고 실행 — 새 통합 테스트가 실제로 `ace-parent.zip`에 대해 통과하는지 확인.
- (선택, 시간 되면) 실제 job으로 재현: job 11이 겪었던 것과 같은 `ace-parent.zip` 업로드 + 출력 버전 `1.0.0` 지정으로 job을 하나 돌려서 `work/pom.xml`의 `ace.version`이 실제로 `1.0.0`인지 직접 확인.
