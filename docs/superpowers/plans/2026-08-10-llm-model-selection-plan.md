# 구현 계획 — 설정 다이얼로그 LLM 모델 선택

스펙: [`docs/superpowers/specs/2026-08-10-llm-model-selection-design.md`](../specs/2026-08-10-llm-model-selection-design.md)

`writing-plans` 스킬이 이 환경에 설치돼 있지 않아(`skills-lock.json`에 `brainstorming`만 등록됨) 기존 계획 문서 형식을 따라 직접 작성했다. 단계는 의존성 순서(설정 필드/파일 쓰기 헬퍼 → API 엔드포인트 → 프론트엔드 → 테스트)를 따른다.

## 0. 사전 확인

- 현재 `git status`가 깨끗한지 확인.
- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 기존 테스트 베이스라인 확인.
- **주의**: 이 기능은 실제 `backend/.env`(시크릿 포함, gitignore됨)에 파일 쓰기를 한다. 구현/수동 테스트 전에 `backend/.env`를 한 번 백업해두거나, 최소한 `LLM_MODEL` 줄 원래 값을 기억해둔다.

## 1. `config.py` — 필드 + `.env` 패치 헬퍼

- `Settings`에 `llm_available_models: str = "gpt-5.4-mini,gpt-4o-mini"` 필드를 기존 `llm_model` 바로 아래에 추가.
- `cors_origins_list` property 바로 아래(또는 근처)에 동일 패턴으로 추가:

```python
@property
def llm_available_models_list(self) -> list[str]:
    return [m.strip() for m in self.llm_available_models.split(",") if m.strip()]
```

- 모듈 하단(`configure_langsmith_env` 근처)에 스펙 코드 그대로 추가:

```python
def write_llm_model_to_env(new_model: str, env_path: Path | None = None) -> None:
    env_path = env_path or (BACKEND_DIR / ".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    new_line = f"LLM_MODEL={new_model}"
    for i, line in enumerate(lines):
        if line.startswith("LLM_MODEL="):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

**검증**: `backend/tests/unit/test_config.py`에 추가 (기존 `test_defaults_load_without_env_file` 등과 같은 스타일):
- `test_llm_available_models_list_defaults_to_two_models`: `Settings(_env_file=None)` → `["gpt-5.4-mini", "gpt-4o-mini"]`.
- `test_llm_available_models_list_parses_custom_csv`: `Settings(_env_file=None, llm_available_models="a, b ,c")` → `["a", "b", "c"]`.
- `test_write_llm_model_to_env_replaces_only_that_line(tmp_path)`: `tmp_path / ".env"`에 `LLM_MODEL=old\nOPENAI_API_KEY=secret\n`을 써두고 `write_llm_model_to_env("new", env_path=...)` 호출 → 파일 내용이 `LLM_MODEL=new\nOPENAI_API_KEY=secret\n`인지(순서·다른 줄 보존).
- `test_write_llm_model_to_env_appends_when_missing(tmp_path)`: `LLM_MODEL=` 줄이 없는 `.env`에 호출 → 끝에 추가되는지.
- `test_write_llm_model_to_env_creates_file_when_missing(tmp_path)`: 파일 자체가 없을 때 → 새로 생성되고 `LLM_MODEL=...` 한 줄만 있는지.
- **`env_path`를 항상 명시적으로 넘겨서 실제 `backend/.env`를 절대 건드리지 않도록 주의.**

## 2. `api/routers/settings.py` (신규) + `main.py` 등록

- 스펙 코드 그대로 라우터 파일 생성 (`GET/POST /settings/llm-model`).
- `main.py`: 반드시 별칭을 써서 import한다 — `create_app()` 안에 이미 `settings = get_settings()`라는 지역 변수가 있어서(line 14), `from app.api.routers import ..., settings`로 그냥 가져오면 `create_app()` 함수 전체에서 `settings`가 그 지역 변수로 해석되어(Python의 함수 스코프 규칙상, 함수 안에서 한 번이라도 대입되면 그 이름은 함수 전체에서 지역 변수로 취급됨) `app.include_router(settings.router)` 시점엔 `settings`가 라우터 모듈이 아니라 `Settings` 인스턴스를 가리켜 `AttributeError`가 난다. `from app.api.routers import artifacts, cache, health, jobs, settings as settings_router` 로 임포트하고 `app.include_router(settings_router.router)`를 추가한다.

**검증**: `backend/tests/integration/test_settings_api.py` 신규 (기존 `test_jobs_api.py`의 `app_client` fixture 패턴 재사용, `monkeypatch`로 `write_llm_model_to_env` 실제 파일쓰기 차단):
- `test_get_llm_model_returns_available_and_current`.
- `test_set_llm_model_to_available_value_updates_current_immediately`: POST 후 같은 `client`로 다시 GET → `current`가 바뀐 값. `write_llm_model_to_env` 호출 인자도 확인.
- `test_set_llm_model_to_unknown_value_returns_400_and_does_not_write`: POST에 목록에 없는 값 → 400, `write_llm_model_to_env`가 호출 안 됐는지, GET의 `current`도 안 바뀌었는지.

## 3. `.env.example` 업데이트

`LLM_MODEL=gpt-5.4-mini` 바로 아래에 추가:

```
LLM_AVAILABLE_MODELS=gpt-5.4-mini,gpt-4o-mini
```

## 4. `assets/common.js` — 설정 모달에 LLM 모델 섹션

- `injectSettingsUI()`의 모달 HTML(`overlay.innerHTML`)에서 "취약점 DB 캐시" `<h3>`/`<div class="field-row">` 블록 뒤, `<div class="modal-close-row">` 앞에 스펙의 `<h3>`+`<select>` 마크업 추가.
- `const llmModelSelect = el("llm-model-select");` 추가 (기존 `cacheStatusText`/`cacheRefreshBtn` 선언부 근처).
- `loadLlmModel()` 함수와 `change` 리스너를 스펙 코드 그대로 추가.
- `settingsBtn.addEventListener("click", ...)` 핸들러 안, 기존 `loadCacheStatus();` 옆에 `loadLlmModel();` 호출 추가.

**검증**: `node --check frontend/assets/common.js`.

## 5. `frontend/README.md` — 체크리스트 추가

스펙 §테스트 계획의 "프론트엔드" 항목을 기존 형식(`- [ ] ...`)으로 추가.

## 6. 전체 검증

- `backend/.venv312/Scripts/python.exe -m pytest -q --basetemp=/c/pytesttmp`로 유닛+통합 전체 통과 확인.
- 실제 `backend/.env`를 미리 백업(0단계)해둔 상태에서, 백엔드(`uvicorn`)와 프론트(정적 서버)를 띄우고:
  1. 설정 모달을 열어 "LLM 모델" 드롭다운에 두 모델이 채워지고 현재 값이 선택돼 있는지.
  2. 다른 모델 선택 → `backend/.env`의 `LLM_MODEL` 줄이 실제로 바뀌는지, 다른 줄(예: `OPENAI_API_KEY`)은 그대로인지 파일로 직접 확인.
  3. 서버를 재시작하지 않고 새 job을 하나 시작해, 로그나 LangSmith 추적 등으로 실제 바뀐 모델이 쓰이는지 확인(가능한 범위에서).
  4. 작업이 끝나면 `.env`를 원래 값으로 되돌려놓는다(0단계에서 백업한 값).
