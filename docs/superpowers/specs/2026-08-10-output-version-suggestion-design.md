# 출력 아티팩트 버전 — 제출 전 자동 제안

## 배경 및 목적

`index.html`의 "출력 아티팩트 버전" 필드는 지금 빈 텍스트 입력 하나뿐이라, 사용자가 대상 프로젝트의 현재 버전을 직접 확인하고 다음 버전을 손으로 계산해 입력해야 한다. 소스(Git URL 또는 ZIP)를 지정한 시점에 프로젝트의 현재 버전을 미리 읽어서, 출력 버전 제안값을 필드에 채워주면 이 수작업을 없앨 수 있다.

이 문서는 그중 "제출 전 제안"만 다룬다. "job 실행 중 `mvn effective-pom` 분석 결과로 제안값을 재확인하는 HITL"은 파이프라인 순서 변경과 새로운 일시정지 상태가 필요한 별도 규모의 작업이라 범위 밖으로 분리했다(후속 작업).

## 범위

- 백엔드: `POST /inspect/artifact-version` 신규 엔드포인트 (job을 생성하지 않는 1회성 조회), `maven_detect.py`에 `read_declared_version()`, `versioning/artifact_version.py`에 `suggest_output_version()` 추가
- 프론트엔드: `index.html`에 "현재 버전 확인" 버튼(Git) + ZIP 파일 선택 시 자동 조회, 출력 버전 필드 자동 채움 + 안내 문구

범위 밖: job 실행 중 `mvn effective-pom` 기반 재확인 HITL(후속 작업), 버전 형식 임의 교정(예: `-RC1`, 4단 버전은 건드리지 않음), `<parent>`의 `<relativePath>`를 따라가 로컬 부모 pom을 실제로 찾아 읽는 것(원문 `<parent><version>` 값을 그대로 쓸 뿐, 실제 상속 해석은 하지 않음).

## 결정 사항

- **job을 만들지 않는 1회성 clone/extract**: 기존 `ingest()`이 하는 일(워크스페이스 생성 → `populate_source` → `detect_maven_project` → `work/` 생성 + git 커밋) 중 `work/` 관련 단계는 건너뛴다 — 버전 하나 읽자고 git init/커밋까지 할 필요는 없다. `populate_source`(clone_git 또는 extract_zip 재사용)와 `detect_maven_project`만 재사용하고, 끝나면 임시 디렉터리를 즉시 삭제한다.
- **모든 실패는 조용히 "제안 없음"으로 처리**: Gradle 프로젝트, 잘못된 URL, 클론 실패, 업로드 용량 초과 등(`IngestError`와 그 하위 클래스 전부)을 잡아서 `detected_version: null`로 응답한다. 이 엔드포인트가 실패해도 실제 작업 제출에는 전혀 영향이 없어야 한다.
- **버전 탐색 순서**: 루트 `pom.xml`의 `<version>` → 없으면 `<parent><version>`. `<parent><relativePath>`를 따라가 실제 로컬 부모 pom을 찾지는 않는다(대부분의 프로젝트가 최상위 pom에 직접 버전을 갖고 있거나, 자기 저장소 안의 부모를 가리키는 경우가 흔하지만, 완벽한 처리는 `mvn effective-pom`이 있어야 가능 — 그건 후속 HITL 작업의 몫).
- **제안값은 보수적으로만 정규화**: `-SNAPSHOT` 접미사 제거, `MAJOR.MINOR` 2단 버전은 `MAJOR.MINOR.0`으로 보정. 그 외(예: `-RC1`, `-beta`, 4단 버전, 숫자가 아닌 값)는 원문 그대로 제안값으로 쓴다 — 임의로 버전을 "판단"해서 올리는(major/minor 승격 등) 로직은 넣지 않는다. 잘못 올렸다가 사용자가 못 알아채는 게 더 위험하다.
- **Git은 버튼, ZIP은 자동**: Git 클론은 수 초가 걸릴 수 있어 사용자가 명시적으로 "현재 버전 확인" 버튼을 눌러야 조회된다. ZIP은 이미 로컬 파일이라 대기가 없으므로 파일 선택 즉시 자동 조회한다.
- **필드를 덮어쓰지 않음**: 사용자가 이미 출력 버전 필드에 직접 값을 입력해뒀으면 제안값으로 덮어쓰지 않는다.

## 백엔드 설계

### `ingest/maven_detect.py` — 버전 판독

```python
def read_declared_version(root_pom: Path) -> tuple[str | None, str]:
    """Returns (version, source). source is "version" if root_pom declares
    its own <version>, "parent.version" if only inherited via <parent>
    (the literal XML value, not mvn-resolved), or "none" if neither is
    present."""
    root = _parse_pom(root_pom)
    version = _text(root, "version")
    if version:
        return version, "version"
    parent_el = root.find("{*}parent")
    if parent_el is not None:
        parent_version = _text(parent_el, "version")
        if parent_version:
            return parent_version, "parent.version"
    return None, "none"
```

기존 `_parse_pom`/`_text` 헬퍼(이미 이 파일에 있음, `{*}` 와일드카드 네임스페이스 매치로 pom.xml의 기본 xmlns 처리)를 그대로 재사용한다.

### `versioning/artifact_version.py` — 제안값 정규화

```python
_SNAPSHOT_SUFFIX = "-SNAPSHOT"

def suggest_output_version(declared_version: str) -> str:
    """Normalizes a raw pom.xml version into a release-ready suggestion:
    drops a -SNAPSHOT qualifier, pads an incomplete MAJOR.MINOR into
    MAJOR.MINOR.0. Anything else (other qualifiers, 4-part versions,
    non-numeric values) passes through unchanged -- guessing wrong here is
    worse than not guessing."""
    version = declared_version
    if version.endswith(_SNAPSHOT_SUFFIX):
        version = version[: -len(_SNAPSHOT_SUFFIX)]
    parts = version.split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        version = f"{version}.0"
    return version
```

### `checkpoint/git_repo.py` — `rmtree_clear_readonly` 공유 위치로 이동

`api/routers/jobs.py`의 `_rmtree_clear_readonly`(Windows에서 git의 읽기전용 `.git/objects/**` 파일 때문에 `shutil.rmtree`가 실패하는 문제 대응)를 여기로 옮겨 공개 함수 `rmtree_clear_readonly`로 만든다. `jobs.py`의 `delete_job`과 이번에 새로 추가하는 조회 엔드포인트 양쪽 다 git 저장소가 있을 수 있는 디렉터리를 지우므로, 같은 문제를 겪는다. `jobs.py`는 이 함수를 import해서 쓰도록 바꾸고 자체 정의는 지운다.

### `api/routers/inspect.py` (신규)

```python
router = APIRouter(prefix="/inspect", tags=["inspect"], dependencies=[Depends(require_api_token)])


class VersionPeekResponse(BaseModel):
    detected_version: str | None
    suggested_version: str | None
    source: str  # "version" | "parent.version" | "none"


@router.post("/artifact-version", response_model=VersionPeekResponse)
async def peek_artifact_version(
    git_url: Annotated[str | None, Form()] = None,
    git_ref: Annotated[str | None, Form()] = None,
    zip_file: Annotated[UploadFile | None, File()] = None,
    settings: Settings = Depends(get_settings),
) -> VersionPeekResponse:
    if bool(git_url) == bool(zip_file):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provide exactly one of git_url or zip_file")

    peek_id = uuid.uuid4().hex
    paths = create_job_workspace(f"_peek_{peek_id}", settings)
    tmp_zip: Path | None = None
    try:
        if git_url:
            spec = GitSourceSpec(url=git_url, ref=git_ref)
        else:
            tmp_zip = settings.jobs_dir / f"_peek_upload_{peek_id}.zip"
            with tmp_zip.open("wb") as f:
                shutil.copyfileobj(zip_file.file, f)
            spec = ZipSourceSpec(zip_path=tmp_zip)

        populate_source(paths, spec, settings)
        detection = detect_maven_project(paths.source)
        version, source = read_declared_version(detection.root_pom)
    except IngestError:
        version, source = None, "none"
    finally:
        shutil.rmtree(paths.root, onexc=rmtree_clear_readonly)
        if tmp_zip is not None:
            tmp_zip.unlink(missing_ok=True)

    suggested = suggest_output_version(version) if version else None
    return VersionPeekResponse(detected_version=version, suggested_version=suggested, source=source)
```

`main.py`에 다른 라우터들과 같은 방식으로 등록한다.

**[구현 중 발견한 이슈]** 초안에는 `shutil.rmtree(..., onexc=rmtree_clear_readonly, ignore_errors=True)`처럼 둘을 같이 썼는데, `ignore_errors=True`가 켜지면 `onexc` 콜백 자체가 아예 호출되지 않는다(파이썬 공식 문서: "If ignore_errors is set, errors are ignored; otherwise, if onexc ... is set, it is called"). 즉 Windows 읽기전용 파일 재시도 로직이 조용히 무력화되는 버그였다 — `ignore_errors`는 빼고 `onexc`만 쓰도록 수정(`jobs.py`의 `delete_job`과 동일 패턴).

## 프론트엔드 설계

### `index.html`

Git 필드 줄에 버튼 추가:

```html
<div class="field-row" id="git-fields">
  <label for="git-url">Git URL</label>
  <input id="git-url" type="text" placeholder="https://git.example.com/team/service.git" />
  <label for="git-ref">브랜치/태그 (선택)</label>
  <input id="git-ref" type="text" placeholder="main" />
  <button type="button" id="check-version-btn" class="secondary">현재 버전 확인</button>
</div>
```

출력 버전 필드 아래에 안내 문구 자리 추가:

```html
<div class="field-row">
  <label for="output-version">출력 아티팩트 버전 (선택, 예: 1.0.0)</label>
  <input id="output-version" type="text" placeholder="비어있으면 원본 버전 유지" />
</div>
<p id="version-hint" class="field-hint hidden"></p>
```

### `assets/app.js`

```javascript
async function peekArtifactVersion() {
  const sourceType = document.querySelector('input[name="source-type"]:checked').value;
  const fd = new FormData();
  if (sourceType === "git") {
    if (!gitUrlInput.value.trim()) return;
    fd.append("git_url", gitUrlInput.value.trim());
    if (gitRefInput.value.trim()) fd.append("git_ref", gitRefInput.value.trim());
  } else {
    if (!zipFileInput.files[0]) return;
    fd.append("zip_file", zipFileInput.files[0]);
  }

  versionHint.textContent = "현재 버전 확인 중...";
  versionHint.classList.remove("hidden");
  checkVersionBtn.disabled = true;
  try {
    const res = await fetch(apiUrl("/inspect/artifact-version"), { method: "POST", headers: authHeaders(), body: fd });
    const body = await res.json();
    if (!res.ok || !body.detected_version) {
      versionHint.textContent = "현재 버전을 확인하지 못했습니다.";
      return;
    }
    const sourceNote = body.source === "parent.version" ? " (parent에서 상속)" : "";
    versionHint.textContent = `감지된 현재 버전: ${body.detected_version}${sourceNote}`;
    if (!outputVersionInput.value.trim() && body.suggested_version) {
      outputVersionInput.value = body.suggested_version;
    }
  } catch (err) {
    versionHint.textContent = "현재 버전을 확인하지 못했습니다.";
  } finally {
    checkVersionBtn.disabled = false;
  }
}

checkVersionBtn.addEventListener("click", peekArtifactVersion);
zipFileInput.addEventListener("change", peekArtifactVersion);
```

## 에러 처리 / 엣지 케이스

- Git URL이 비어있는데 버튼 클릭 → 아무 요청도 보내지 않고 조용히 리턴(폼 자체 검증은 기존 제출 시점 로직이 담당).
- Gradle 프로젝트, 존재하지 않는 URL, 클론 타임아웃, ZIP 용량 초과 등 → `detected_version: null` 응답, 프론트는 "현재 버전을 확인하지 못했습니다" 표시, 제출은 그대로 가능.
- `pom.xml`에 `<version>`도 `<parent><version>`도 없는 경우(드묾) → `source: "none"`, 위와 동일하게 처리.
- 사용자가 출력 버전 필드에 이미 값을 입력한 뒤 "현재 버전 확인"을 누른 경우 → 필드는 덮어쓰지 않지만, 안내 문구(감지된 현재 버전)는 갱신한다.
- ZIP을 여러 번 다시 선택하거나 Git URL을 바꾼 뒤 다시 확인 → 매번 새 임시 디렉터리를 만들고 끝나면 지우므로 이전 조회와 충돌하지 않는다.

## 테스트 계획

**단위**:
- `read_declared_version`: `<version>` 직접 선언 → `("1.2.3", "version")`. `<version>` 없고 `<parent><version>`만 있음 → `("1.2.3", "parent.version")`. 둘 다 없음 → `(None, "none")`.
- `suggest_output_version`: `"1.2.3-SNAPSHOT"` → `"1.2.3"`. `"1.2"` → `"1.2.0"`. `"1.2.3"` → `"1.2.3"`(변화 없음). `"1.2.3-RC1"` → `"1.2.3-RC1"`(변화 없음, 임의 교정 안 함).

**통합** (`backend/tests/integration/test_inspect_api.py` 신규, ZIP 업로드 경로 위주 — Git clone은 네트워크가 필요해 기존 job 테스트들처럼 실제 URL을 쓰지 않고 ZIP만으로 커버):
- 유효한 Maven ZIP(`<version>` 있음) 업로드 → 200, `detected_version`/`suggested_version` 일치.
- Gradle ZIP 업로드 → 200, `detected_version: null`(에러 아님, 조용히 처리).
- `git_url`/`zip_file` 둘 다 없음 또는 둘 다 있음 → 400.
- 호출 후 `backend/data/jobs/`에 `_peek_*` 임시 디렉터리가 남아있지 않은지(정리 확인).

**프론트엔드** (수동 스모크, `frontend/README.md` 체크리스트에 추가):
- ZIP 업로드 선택 시 자동으로 "현재 버전 확인 중..." → 감지된 버전 문구로 바뀌고, 출력 버전 필드가 비어있었다면 채워지는지.
- Git URL 입력 후 "현재 버전 확인" 버튼 클릭 시 동일하게 동작하는지.
- 출력 버전 필드에 이미 값을 입력한 상태에서 확인 버튼을 눌러도 필드 값이 안 바뀌는지.
- 존재하지 않는 Git URL로 확인 시도 → "확인하지 못했습니다" 문구만 뜨고 폼 제출 자체는 여전히 가능한지.
