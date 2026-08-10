# 구현 계획 — 출력 아티팩트 버전 제출 전 자동 제안

스펙: [`docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md`](../specs/2026-08-10-output-version-suggestion-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아 기존 계획 문서 형식을 따라 직접 작성했다. 단계는 의존성 순서(공유 헬퍼 이동 → 버전 판독/제안 함수 → API 엔드포인트 → 프론트엔드 → 테스트)를 따른다.

## 0. 사전 확인

- `git status` 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 베이스라인 확인.

## 1. `checkpoint/git_repo.py` — `rmtree_clear_readonly` 이동

- `api/routers/jobs.py`의 `_rmtree_clear_readonly` 함수(및 그 위에 필요한 `import os`, `import stat`)를 `checkpoint/git_repo.py`로 옮기고 공개 이름 `rmtree_clear_readonly`로 바꾼다.
- `jobs.py`에서: 자체 정의 삭제, `from app.checkpoint.git_repo import rmtree_clear_readonly` 추가, `delete_job`의 `shutil.rmtree(job_dir, onexc=_rmtree_clear_readonly)` 호출을 `onexc=rmtree_clear_readonly`로 수정.

**검증**: `backend/tests/integration/test_jobs_api.py`의 기존 `test_delete_terminal_job_removes_row_and_directory`가 여전히 통과하는지(이 함수가 실제로 쓰이는 유일한 기존 테스트).

## 2. `ingest/maven_detect.py` — `read_declared_version`

스펙 코드 그대로 `detect_maven_project` 아래에 추가.

**검증**: `backend/tests/unit/test_maven_detect.py`에 추가 (기존 테스트들의 임시 pom.xml 작성 패턴 재사용):
- `test_read_declared_version_from_own_version_tag`: `<version>1.2.3</version>` 직접 선언 → `("1.2.3", "version")`.
- `test_read_declared_version_falls_back_to_parent_version`: 자체 `<version>` 없고 `<parent><version>1.2.3</version></parent>`만 있음 → `("1.2.3", "parent.version")`.
- `test_read_declared_version_returns_none_when_absent`: 둘 다 없음 → `(None, "none")`.

## 3. `versioning/artifact_version.py` — `suggest_output_version`

스펙 코드 그대로 추가.

**검증**: `backend/tests/unit/test_artifact_version.py`에 추가:
- `"1.2.3-SNAPSHOT"` → `"1.2.3"`.
- `"1.2"` → `"1.2.0"`.
- `"1.2.3"` → `"1.2.3"` (변화 없음).
- `"1.2.3-RC1"` → `"1.2.3-RC1"` (임의 교정 안 함, 변화 없음).
- `"1.2.3.4"`(4단) → `"1.2.3.4"` (변화 없음).

## 4. `api/routers/inspect.py` (신규) + `main.py` 등록

- 스펙 코드 그대로 라우터 작성. Import 목록: `uuid`, `shutil`, `Path`, `Annotated`, FastAPI 관련, `pydantic.BaseModel`, `app.api.deps.require_api_token`, `app.checkpoint.git_repo.rmtree_clear_readonly`, `app.config.Settings/get_settings`, `app.ingest.errors.IngestError`, `app.ingest.maven_detect.detect_maven_project/read_declared_version`, `app.ingest.workspace.GitSourceSpec/ZipSourceSpec/create_job_workspace/populate_source`, `app.versioning.artifact_version.suggest_output_version`.
- `main.py`: `from app.api.routers import inspect as inspect_router`(다른 라우터와 이름 충돌 없음 확인 — `jobs.py`에서 이미 `inspect` 같은 이름의 지역 변수가 없는지 grep으로 재확인) 후 `app.include_router(inspect_router.router)` 추가.

**검증**: `backend/tests/integration/test_inspect_api.py` 신규 (기존 `test_jobs_api.py`의 `_zip_bytes`/`app_client` 픽스처 스타일 재사용):
- 유효한 Maven ZIP(`pom.xml`에 `<version>1.0.0</version>` 포함) 업로드 → 200, `detected_version == "1.0.0"`, `suggested_version == "1.0.0"`, `source == "version"`.
- `<version>` 없이 `<parent><version>2.0.0-SNAPSHOT</version></parent>`만 있는 ZIP → `detected_version == "2.0.0-SNAPSHOT"`, `suggested_version == "2.0.0"`, `source == "parent.version"`.
- Gradle 프로젝트(= `build.gradle`만 있고 `pom.xml` 없음) ZIP 업로드 → 200(에러 아님), `detected_version is None`.
- `git_url`/`zip_file` 둘 다 없음, 또는 둘 다 있음 → 400.
- 호출 후 `tmp_path / "jobs"` 아래에 `_peek_*` 디렉터리나 `_peek_upload_*.zip`이 남아있지 않은지(정리 확인 — `Settings.jobs_dir` 기준으로 glob).

## 5. `index.html` — 버튼/안내 문구 마크업

- `git-fields`의 `field-row`에 `<button type="button" id="check-version-btn" class="secondary">현재 버전 확인</button>` 추가(스펙 코드 그대로, `git-ref` 입력 뒤).
- "출력 아티팩트 버전" `field-row` 바로 아래에 `<p id="version-hint" class="field-hint hidden"></p>` 추가.

**검증**: 브라우저에서 마크업만 확인(다음 단계에서 wiring).

## 6. `assets/app.js` — 조회 로직

- 상단 참조에 `const checkVersionBtn = el("check-version-btn");`, `const versionHint = el("version-hint");` 추가.
- `peekArtifactVersion()` 함수와 이벤트 리스너 두 개(`checkVersionBtn` click, `zipFileInput` change)를 스펙 코드 그대로 추가.

**검증**: `node --check frontend/assets/app.js`. 실사용 확인은 §8.

## 7. `frontend/README.md` — 체크리스트 추가

스펙 §테스트 계획의 "프론트엔드" 항목을 기존 형식으로 추가.

## 8. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인.
- 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고:
  1. ZIP 파일 선택 시 자동으로 "확인 중..." → 감지된 버전으로 바뀌고, 출력 버전 필드가 비어있었다면 채워지는지.
  2. Git URL 입력 후 "현재 버전 확인" 버튼 클릭 시 동일하게 동작하는지(실제 접근 가능한 저장소로).
  3. 출력 버전 필드에 값을 미리 입력해두고 확인 버튼을 눌러도 필드가 안 바뀌는지(안내 문구만 갱신).
  4. 존재하지 않는 URL로 확인 시도 → "확인하지 못했습니다" 문구, 폼 제출 자체는 여전히 가능한지.
  5. `backend/data/jobs/` 아래에 확인 후 `_peek_*` 잔여물이 없는지 직접 확인.

## 참고 — 스펙에서 구현 단계로 넘어오며 확정해야 할 세부사항

- `create_job_workspace`가 `source/`/`work/`/`output/` 세 디렉터리를 다 만들지만 이 기능은 `source/`만 쓴다 — `work/`/`output/`는 빈 채로 남았다가 `finally`의 `shutil.rmtree`로 통째로 지워지므로 낭비이긴 해도 해될 건 없다. 별도로 "source만 만드는" 헬퍼를 새로 만들지 여부는 구현 시 판단하되, 기존 함수 재사용 쪽을 기본으로 한다(YAGNI).
- 임시 워크스페이스 이름은 `_peek_{uuid.uuid4().hex}` (job_id와 겹치지 않는 접두사 — `next_job_id()`가 정수 문자열만 만들므로 `_`로 시작하는 이 값과 절대 충돌하지 않는다).
