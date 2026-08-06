# ACE Upgrade Tool — Backend

사내 Maven 시스템을 Java 21 / Spring Boot 4.1 / Spring Cloud 2025.1 / Spring AI 2.0으로 마이그레이션하고, 남은 개별 OSS 취약점을 패치하는 도구의 백엔드(FastAPI)입니다.

설계 배경은 [`docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md`](../docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md)를 참고하세요.

> 이 도구는 중앙 서버가 아니라 **각 시스템 유지보수 담당자(개발자)가 자기 PC에서 직접 실행**하는 것을 전제로 합니다.

## 1. 사전 준비 확인

아래 5개가 PATH에 잡혀 있어야 합니다. 한 번에 확인하려면:

```bash
# Windows (PowerShell)
./scripts/check-prereqs.ps1

# Mac/Linux
./scripts/check-prereqs.sh
```

| 확인 명령 | 용도 |
|---|---|
| `java -version` | 대상 프로젝트 빌드/검증(`mvn compile`/`test`/`verify`) 및 최종 목표(Java 21)용 JDK |
| `mvn -version` | 대상 프로젝트의 Maven 빌드, OpenRewrite(`mvn rewrite:run`), Maven Versions Plugin 실행 |
| `python --version` | 이 도구 자신(FastAPI 백엔드)의 실행 (3.11+) |
| `trivy --version` | 2단계 취약점 스캔 |
| `npm -v` | 대상 프로젝트에 `frontend-maven-plugin` 기반 프론트엔드 모듈이 있어 빌드/검증 시 필요할 수 있음 |

각 항목이 없거나 버전이 안 맞으면, 스크립트가 설치 방법(공식 배포처 링크)을 함께 안내합니다.

## 2. 설치

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## 3. 설정 (.env)

```bash
cp .env.example .env
```

`.env`를 열어 최소한 아래 값을 채우세요 (`../draft/.env`에 실제 값 예시가 있습니다 — 이 파일은 절대 커밋하지 마세요):

- `OPENAI_API_KEY` — LLM 오케스트레이션용
- `NVD_API_KEY` — 2단계 취약점 스캔(OWASP Dependency-Check)용
- `API_AUTH_TOKEN` — 비워두면 인증 없이 실행됩니다(로컬 단독 실행 시 기본값으로 무방하나, 시작 로그에 경고가 남습니다)

사내망 프록시를 쓰는 환경이면 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`도 채워주세요.

## 4. 실행

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8010
```

`GET /health` — 정상 기동 확인. `GET /prereqs` — 위 사전 준비 상태를 API로도 확인 가능.

**Windows에서 `--reload`를 쓰면 안 됩니다.** uvicorn은 `--reload`(또는 `--workers`>1) 시 워커를 별도 프로세스로 띄우면서 이벤트 루프를 `SelectorEventLoop`로 강제 전환하는데(`use_subprocess=True` 분기), Windows의 `SelectorEventLoop`는 `asyncio` 서브프로세스(`create_subprocess_exec`)를 지원하지 않아 인자 없는 `NotImplementedError`를 던집니다. 이 앱은 `mvn`/OpenRewrite/Trivy를 전부 비동기 서브프로세스로 실행하므로, `--reload`를 쓰면 인입(ingest, git은 동기 호출이라 통과) 직후 첫 비동기 서브프로세스 호출(예: 출력 아티팩트 버전 설정 단계의 `mvn versions:set`)에서 **원인 메시지가 빈 문자열인 채로 작업이 `failed` 처리**됩니다. 코드를 고치면 화면에서 바로 반영되지 않으니, 수정 후에는 서버를 수동으로 재시작하세요.

## 5. 테스트

```bash
pytest                        # 빠른 단위 테스트만 (기본)
pytest -m slow                # + 실제 mvn/git/java 통합 테스트
pytest -m external            # + 실제 네트워크/시크릿 필요한 테스트 (trivy DB, NVD, OpenAI, LangSmith)
```

> **참고**: `-m external`을 처음 실행하면 OWASP Dependency-Check가 NVD 전체 데이터셋(수십만 건)을 `DEPENDENCY_CHECK_DATA_DIR`로 최초 동기화합니다. 실제로 돌려보니 수십 분 이상 걸릴 수 있습니다(네트워크 상황에 따라 다름). **이미 동기화된 캐시가 있다면 `DEPENDENCY_CHECK_DATA_DIR`(기본 `backend/data/nvd-cache/`)에 그대로 복사해 넣으세요** — 실제로 다른 프로젝트의 캐시(1일 전 것)를 복사해서 재사용해보니, 전체 재동기화 대신 증분 업데이트(수천 건)만 받고 2~3분 만에 끝났습니다. Trivy는 자체 취약점 DB를 훨씬 빠르게(약 10~20초) 받습니다.

## 6. 참고: 대상 프로젝트가 사내 전용 저장소를 참조하는 경우

대상 프로젝트의 `pom.xml`이 사내 전용 Nexus 등 외부에서 접근 불가능한 `<repository>`를 선언하고 있어도, 그 저장소에만 있는 의존성이 아니라면 빌드는 대체로 정상 진행됩니다 — Maven은 각 의존성마다 설정된 저장소를 순서대로 시도하고, 그중 하나(대개 Maven Central)에서 해결되면 나머지 저장소의 실패는 경고로만 남기기 때문입니다. 다만 그 사내 저장소에만 있는 사내 전용 라이브러리(사내 SSO 클라이언트 등)에 의존하는 프로젝트라면, 해당 저장소에 실제로 접근 가능한 네트워크(VPN 등)에서 실행해야 합니다.

OpenRewrite 실행(`rewrite_client.py`) 시 `rewrite-maven-plugin`과 레시피 아티팩트(`recipe_catalog.yaml`의 `artifact` 필드) 버전은 **반드시 서로 맞물려야 합니다** — 실제로 플러그인만 오래된 버전으로 고정하고 레시피 쪽만 `RELEASE`로 흘러가게 뒀더니 `IncompatibleClassChangeError`로 즉시 깨지는 것을 확인했습니다. 지금은 둘 다 `RELEASE`로 맞춰 함께 흘러가게 해뒀습니다.

Dependency-Check 실행(`dependency_check.py`) 시, 멀티모듈 리액터에서 뒤쪽 모듈이 앞쪽 형제 모듈에 의존하면(`ace-ai`가 `ace-common`에 의존하는 식) `dependency-check:check`만 단독 골로 실행할 경우 그 형제 모듈이 아직 빌드/설치되지 않아 의존성 해석에 실패합니다 — 그래서 `install`을 같은 명령에 먼저 실행하도록 했습니다. 또한 `-DoutputDirectory`는 리액터 모듈별로 무시되고 각 모듈이 자기 `target/dependency-check-report.json`에 리포트를 쓰므로, `**/target/dependency-check-report.json`을 전부 찾아 합쳐야 합니다(`scan/combined.py`의 `find_dependency_check_reports`).

`.env`의 `LANGSMITH_*` 값은 `Settings` 객체(`pydantic-settings`)로만 파싱될 뿐 프로세스의 `os.environ`에는 반영되지 않는데, LangChain 자체 자동 트레이싱(`langsmith.utils.get_env_var`)은 `os.environ`을 직접 읽습니다 — 실제로 확인해보니 `.env`에 값을 다 채워도 이 연결 없이는 트레이싱이 조용히 비활성 상태로 남습니다. 그래서 `create_app()` 기동 시 `config.configure_langsmith_env()`가 한 번 `os.environ`에 내보내 줍니다(이미 쉘에서 명시적으로 export된 값은 덮어쓰지 않음).
