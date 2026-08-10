# 설정 다이얼로그 — LLM 모델 선택

## 배경 및 목적

지금 LLM 모델은 `backend/.env`의 `LLM_MODEL` 값 하나로 고정되어 있고, 바꾸려면 파일을 직접 열어 편집한 뒤 서버를 재시작해야 한다. 프론트엔드 설정 다이얼로그(⚙ 아이콘)에서 API 서버 주소·토큰을 바꾸고 취약점 DB 캐시를 갱신할 수 있는 것처럼, 사용 가능한 LLM 모델 중 하나를 골라 즉시 반영할 수 있게 한다.

## 범위

- 백엔드: `Settings`에 사용 가능 모델 목록 필드 추가, `GET/POST /settings/llm-model` 엔드포인트, `.env`의 `LLM_MODEL` 줄을 안전하게 교체하는 헬퍼
- 프론트엔드: 설정 다이얼로그(`common.js`)에 "LLM 모델" 섹션과 `<select>` 추가

범위 밖: 모델별 API 키/엔드포인트 등 다른 LLM 설정 항목의 UI화(지금은 모델명만), 진행 중인 job의 모델을 시작 시점에 고정하는 것(아래 결정 사항 참고), 임의 문자열 모델 입력(목록에 없는 값은 거부).

## 결정 사항

- **사용 가능 모델 목록도 `.env`로 설정**: 새 필드 `LLM_AVAILABLE_MODELS`(콤마 구분)를 추가한다. 값이 없으면 기본값 `gpt-5.4-mini,gpt-4o-mini` 두 개를 보여준다. 기존 `CORS_ALLOW_ORIGINS` → `cors_allow_origins`/`cors_origins_list` property 쌍과 정확히 같은 패턴을 따른다.
- **선택값은 화이트리스트 검증 후 `.env`에 저장**: `POST` 요청의 모델명이 `LLM_AVAILABLE_MODELS` 목록에 없으면 400으로 거부한다. 임의 문자열을 그대로 `.env`에 쓰지 않는다 — 파일에 잘못된 값이나 개행이 섞여 다른 설정 줄을 깨뜨릴 위험을 차단하는 목적도 있다.
- **`.env` 파일은 줄 단위로만 교체**: `LLM_MODEL=` 로 시작하는 줄만 찾아 교체하고(없으면 추가), 나머지 줄(예: `OPENAI_API_KEY`, `GIT_TOKEN` 같은 시크릿)은 절대 건드리지 않는다.
- **재시작 없이 즉시 반영**: `get_settings()`가 `@lru_cache`로 프로세스 생애주기 동안 하나의 `Settings` 인스턴스만 반환한다. `.env` 파일 저장과 별개로, 이 캐시된 인스턴스의 `llm_model` 필드도 함께 갱신해 다음 LLM 호출부터 바로 새 모델을 쓰게 한다. `.env` 저장은 재시작 후에도 값이 유지되게 하기 위함이고, 인스턴스 갱신은 재시작 없이 당장 반영되게 하기 위함 — 둘 다 필요하다.
- **진행 중인 job에 대한 영향은 그대로 둔다**: 모델은 job 시작 시점에 고정되지 않고 매 LLM 호출마다 `settings.llm_model`을 그대로 읽는다. 즉 실행 중 모델을 바꾸면 그 job의 다음 LLM 호출부터 새 모델로 바뀔 수 있다. 로컬 1인 개발자 도구이고 모델 교체는 운영자가 의도적으로 하는 조작이므로, job별로 모델을 고정하는 장치는 만들지 않는다(YAGNI).

## 백엔드 설계

### `config.py`

```python
class Settings(BaseSettings):
    ...
    llm_model: str = "gpt-5.4-mini"
    llm_available_models: str = "gpt-5.4-mini,gpt-4o-mini"
    ...

    @property
    def llm_available_models_list(self) -> list[str]:
        return [m.strip() for m in self.llm_available_models.split(",") if m.strip()]


def write_llm_model_to_env(new_model: str, env_path: Path | None = None) -> None:
    """backend/.env의 LLM_MODEL= 줄만 교체(없으면 추가)한다. 다른 줄(시크릿
    포함)은 건드리지 않는다. 캐시된 Settings 싱글턴 갱신은 호출자(api/
    routers/settings.py) 책임 -- 재시작 없이 즉시 반영하려면 파일 저장과
    별개로 그것도 해야 하기 때문. env_path는 테스트에서 실제 backend/.env
    대신 임시 파일을 주입하기 위한 용도(기본값은 실제 경로)."""
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

### `api/routers/settings.py` (신규)

```python
router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_api_token)])


class LlmModelResponse(BaseModel):
    available: list[str]
    current: str


class SetLlmModelRequest(BaseModel):
    model: str


@router.get("/llm-model", response_model=LlmModelResponse)
async def get_llm_model(settings: Settings = Depends(get_settings)) -> LlmModelResponse:
    return LlmModelResponse(available=settings.llm_available_models_list, current=settings.llm_model)


@router.post("/llm-model", response_model=LlmModelResponse)
async def set_llm_model(body: SetLlmModelRequest, settings: Settings = Depends(get_settings)) -> LlmModelResponse:
    if body.model not in settings.llm_available_models_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown model: {body.model!r}; available: {settings.llm_available_models_list}",
        )
    write_llm_model_to_env(body.model)
    settings.llm_model = body.model  # Depends(get_settings)가 반환한 건 캐시된 싱글턴 그 자체이므로,
    # 이 대입이 곧 프로세스 전체에 즉시 반영된다 -- 재시작 불필요.
    return LlmModelResponse(available=settings.llm_available_models_list, current=settings.llm_model)
```

`main.py`에 다른 라우터들과 같은 방식으로 등록한다.

## 프론트엔드 설계

`assets/common.js`의 설정 모달(`injectSettingsUI`)에 "취약점 DB 캐시" 섹션 뒤, 닫기 버튼 앞에 추가:

```html
<h3 class="modal-section-heading">LLM 모델</h3>
<div class="field-row">
  <label for="llm-model-select">사용할 모델</label>
  <select id="llm-model-select"></select>
</div>
```

```javascript
const llmModelSelect = el("llm-model-select");

async function loadLlmModel() {
  try {
    const res = await fetch(apiUrl("/settings/llm-model"), { headers: authHeaders() });
    if (!res.ok) return;
    const body = await res.json();
    llmModelSelect.innerHTML = "";
    body.available.forEach((model) => {
      const opt = document.createElement("option");
      opt.value = model;
      opt.textContent = model;
      if (model === body.current) opt.selected = true;
      llmModelSelect.appendChild(opt);
    });
  } catch (err) {
    // 조용히 실패 -- select가 비어있는 채로 남고, 캐시 상태 등 모달의 나머지 기능은 그대로 동작
  }
}

llmModelSelect.addEventListener("change", async () => {
  const chosen = llmModelSelect.value;
  try {
    const res = await fetch(apiUrl("/settings/llm-model"), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ model: chosen }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    alert(`모델 변경 실패: ${err.message}`);
    loadLlmModel(); // 실패했으니 select를 실제 현재값으로 되돌림
  }
});
```

모달을 여는 기존 핸들러(`settingsBtn.addEventListener("click", ...)`)에서 `loadCacheStatus()` 옆에 `loadLlmModel()`도 호출한다.

## 에러 처리 / 엣지 케이스

- `available` 목록에 없는 모델명 요청 → 400, `.env`는 건드리지 않음.
- `.env` 파일이 아직 없는 상태(신규 클론 등) → `write_llm_model_to_env`가 새로 만들어 `LLM_MODEL=` 한 줄만 있는 파일을 생성. 나머지 설정은 pydantic-settings 기본값으로 채워지므로 기능상 문제 없음(단, 다음 서버 재시작 전까지는 다른 필드가 `.env`에 반영 안 된 상태로 남지만, 이건 이 기능이 만든 상태가 아니라 애초에 `.env` 없이 기본값으로 돌던 상태의 연장선).
- 설정 모달을 여는 시점에 `GET /settings/llm-model` 실패(네트워크 오류 등) → `<select>`가 비어있는 채로 남고 나머지 모달 기능(캐시 상태 등)은 정상 동작.
- `API_AUTH_TOKEN`이 설정된 백엔드에 잘못된 토큰으로 요청 → 기존 `require_api_token` 의존성이 401을 반환(다른 라우터와 동일).

## 테스트 계획

**단위** (`backend/tests/unit/test_config.py`에 추가, 없으면 새로 생성):
- `llm_available_models_list`: 기본값(설정 안 함) → `["gpt-5.4-mini", "gpt-4o-mini"]`. 커스텀 값(`"a, b ,c"`) → 공백 제거된 `["a", "b", "c"]`.
- `write_llm_model_to_env`: 기존 `LLM_MODEL=` 줄이 있는 `.env`를 교체했을 때 그 줄만 바뀌고 다른 줄(예: `OPENAI_API_KEY=secret`)은 그대로인지. 줄이 없는 `.env`에서는 추가되는지. 파일이 아예 없을 때는 새로 생성되는지.

**통합** (`backend/tests/integration/test_settings_api.py` 신규): 라우터가 기본적으로 실제 `backend/.env`를 건드리므로, `monkeypatch.setattr("app.api.routers.settings.write_llm_model_to_env", ...)`로 실제 파일 쓰기를 가로막고 호출 여부/인자만 검증한다(개발자의 실제 `.env`가 테스트 중 덮어써지면 안 됨).
- `GET /settings/llm-model` → 200, `available`/`current`가 설정값과 일치.
- `POST /settings/llm-model`에 목록에 있는 값 → 200, `write_llm_model_to_env`가 그 값으로 호출됐는지, 이후 같은 프로세스 안에서 `GET /settings/llm-model`의 `current`가 바뀐 값으로 나오는지(재시작 없이 즉시 반영 확인).
- `POST /settings/llm-model`에 목록에 없는 값 → 400, `write_llm_model_to_env`가 호출되지 않았는지, 캐시된 설정도 바뀌지 않았는지.

**프론트엔드** (수동 스모크, `frontend/README.md` 체크리스트에 추가):
- 설정 모달을 열면 "LLM 모델" 섹션에 드롭다운이 채워지고 현재 모델이 선택돼 있는지.
- 드롭다운에서 다른 모델 선택 → 별다른 확인 없이 바로 반영되는지(추가 저장 버튼 없음), `backend/.env`의 `LLM_MODEL` 줄이 실제로 바뀌었는지 파일로 확인.
