# 사내 적용 고려사항

## 1. 목적

현재 사외 환경에서 개발 중인 Maven Stack Upgrade Tool을 아래 두 사내 private 환경에 적용하기 위해 확인할 사항과 환경별 차이를 지속적으로 관리한다.

- POSCO 환경
- POSCODX 환경

이 문서는 확정된 설정값만 기록하는 최종 운영 명세가 아니다. 현재 코드에서 확인한 사실, 환경 담당자에게 확인할 질문, 사내 반입 전에 검증할 항목을 함께 관리하는 살아 있는 문서다.

## 2. 기록 원칙

### 2.1 상태

| 상태 | 의미 |
|---|---|
| 확정 | 코드 또는 환경 정보로 사실이 확인됨 |
| 일부 확인 | 공통 방향은 확인됐지만 환경별 값이나 세부 제약은 확인되지 않음 |
| 조사 필요 | 환경 담당자 확인 또는 실제 접속 시험이 필요함 |
| 해당 없음 | 해당 환경에서는 사용하지 않기로 확인됨 |

### 2.2 작성 규칙

- 모르는 값은 빈칸이나 `TBD`로 두지 않고 `조사 필요`라고 기록한다.
- 추정 내용은 `가정`이라고 표시하고, 확인 후 상태와 내용을 갱신한다.
- 두 환경의 값이 같더라도 POSCO와 POSCODX 열에 각각 기록한다.
- 실제 URL, API Key, 토큰, 비밀번호, 인증서 본문 등 민감정보는 이 문서에 기록하지 않는다.
- 민감정보 대신 환경변수 이름, Secret 저장 위치, 발급 및 갱신 담당을 기록한다.
- 확인된 사실에는 관련 코드 위치 또는 확인 방법을 함께 남긴다.

## 3. 환경별 핵심 차이 요약

| 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 |
|---|---|---|---|---|
| LLM API 주소 | OpenAI 또는 OpenAI 호환 API 사용 | 별도 `base_url` 사용 | POSCO와 다른 별도 `base_url` 사용 | 일부 확인 |
| LLM 인증 및 모델 | `OPENAI_API_KEY`, `LLM_MODEL` 설정 | 인증 방식과 모델명 조사 필요 | 인증 방식과 모델명 조사 필요 | 조사 필요 |
| 외부망 접근 | OpenAI, LangSmith, NVD, Trivy DB, Maven Central 등에 접근 가능하다고 가정 | 허용 대상과 차단 정책 조사 필요 | 허용 대상과 차단 정책 조사 필요 | 조사 필요 |
| 패키지·아티팩트 저장소 | 공개 PyPI/Maven Central 사용 가능 | 사내 저장소와 인증 방식 조사 필요 | 사내 저장소와 인증 방식 조사 필요 | 조사 필요 |
| 배포 형태 | 개발자 PC에서 로컬 실행 | 실행 위치와 운영 방식 조사 필요 | 실행 위치와 운영 방식 조사 필요 | 조사 필요 |

## 4. 분야별 검토표

### 4.1 LLM 연결과 `ChatOpenAI`

현재 `backend/app/orchestration/llm.py`의 `get_chat_model()`이 `ChatOpenAI(**kwargs)`를 생성한다. 기본 전달값은 `model`, `api_key`, `max_completion_tokens`이며, `LLM_BASE_URL`이 설정된 경우에만 `base_url`을 추가한다.

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| API `base_url` | `LLM_BASE_URL`로 선택 설정 | 별도 주소 사용 | POSCO와 다른 별도 주소 사용 | 일부 확인 | `backend/app/config.py`, `backend/app/orchestration/llm.py` | 각 환경 LLM 담당자 | 실제 주소는 Secret 또는 배포 설정에서 관리 |
| 인증 방식 | `OPENAI_API_KEY`를 `api_key`로 전달 | API Key, Bearer Token, 사내 인증 헤더 여부 조사 필요 | API Key, Bearer Token, 사내 인증 헤더 여부 조사 필요 | 조사 필요 | `backend/app/orchestration/llm.py` | LLM Gateway 담당자 | 커스텀 헤더가 필요하면 `kwargs` 확장 필요 |
| 모델 식별자 | 기본값 `gpt-5.4-mini` | 제공 모델명 및 배포명 조사 필요 | 제공 모델명 및 배포명 조사 필요 | 조사 필요 | `backend/app/config.py` | LLM Gateway 모델 목록 조회 | Azure 호환 API라면 모델명 대신 deployment 명칭일 수 있음 |
| 지원 파라미터 | `max_completion_tokens` 사용 | 지원 여부와 대체 파라미터 조사 필요 | 지원 여부와 대체 파라미터 조사 필요 | 조사 필요 | `backend/app/orchestration/llm.py` | 최소 호출 시험 | 환경에 따라 `max_tokens`만 지원할 수 있음 |
| API 호환성 | OpenAI 호환 Chat Completions 전제 | API 경로·버전·응답 형식 조사 필요 | API 경로·버전·응답 형식 조사 필요 | 조사 필요 | `backend/app/orchestration/llm.py` | 샘플 요청 및 LangChain 연동 시험 | 스트리밍, tool calling 지원 여부 포함 |
| Tool calling | Stage 1·2 AI 수정에서 사용 | 지원 모델과 도구 호출 형식 조사 필요 | 지원 모델과 도구 호출 형식 조사 필요 | 조사 필요 | `backend/app/orchestration/graph_stage1.py`, `graph_stage2.py` | 실제 에이전트 smoke test | 단순 채팅 성공만으로는 충분하지 않음 |
| TLS 검증 | 시스템 기본 CA 사용 | 사내 CA 설치 또는 CA bundle 경로 조사 필요 | 사내 CA 설치 또는 CA bundle 경로 조사 필요 | 조사 필요 | Python/httpx 실행 환경 | 보안·인프라 담당자 | 인증서 검증 비활성화는 기본 대안으로 사용하지 않음 |
| 타임아웃·재시도 | 라이브러리 기본 동작 중심 | Gateway 제한과 권장값 조사 필요 | Gateway 제한과 권장값 조사 필요 | 조사 필요 | `backend/app/orchestration/llm.py` | 장애·지연 시험 | 429, 5xx, 연결 지연 처리 기준 필요 |
| 사용량·비용 제한 | 월 예산 설정 필드 존재 | 쿼터·호출 제한 조사 필요 | 쿼터·호출 제한 조사 필요 | 조사 필요 | `backend/app/config.py` | 플랫폼 운영 정책 확인 | `LLM_MONTHLY_BUDGET_USD`는 현재 강제 로직 여부 추가 확인 필요 |

### 4.2 인증과 Secret 관리

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| 설정 주입 | `backend/.env`를 `pydantic-settings`로 로드 | 배포 플랫폼의 Secret 주입 방식 조사 필요 | 배포 플랫폼의 Secret 주입 방식 조사 필요 | 조사 필요 | `backend/app/config.py` | 배포·보안 담당자 | 운영 환경에서 `.env` 파일 직접 배포 여부 결정 |
| API 인증 | `API_AUTH_TOKEN` 단일 Bearer Token | SSO, Gateway 인증, 서비스 계정 정책 조사 필요 | SSO, Gateway 인증, 서비스 계정 정책 조사 필요 | 조사 필요 | `backend/app/api/deps.py` | 보안 담당자 | 사용자별 권한과 감사 추적 필요 여부 포함 |
| Secret 회전 | 별도 자동화 없음 | 발급·보관·회전·폐기 절차 조사 필요 | 발급·보관·회전·폐기 절차 조사 필요 | 조사 필요 | 배포 설정 | Secret 관리 담당자 | 재기동 없이 갱신해야 하는지 확인 |
| 프론트 토큰 저장 | 브라우저 `localStorage`에 저장 | 보안 정책 허용 여부 조사 필요 | 보안 정책 허용 여부 조사 필요 | 조사 필요 | `frontend/assets/common.js` | 웹 보안 담당자 | XSS 발생 시 노출 위험 검토 |
| 로그 내 Secret | 애플리케이션 로그 정책 추가 확인 필요 | 마스킹 기준 조사 필요 | 마스킹 기준 조사 필요 | 조사 필요 | `backend/app/logging_conf.py`, 산출 로그 | 보안 담당자 | URL query, header, 환경변수 출력 금지 |

### 4.3 네트워크·DNS·프록시·TLS

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| HTTP/HTTPS 프록시 | `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` 지원 | 프록시 주소와 예외 대상 조사 필요 | 프록시 주소와 예외 대상 조사 필요 | 일부 확인 | `backend/app/config.py`, `backend/app/procenv.py` | 네트워크 담당자 | Python LLM 호출에도 동일 설정이 적용되는지 시험 필요 |
| DNS | OS 설정 사용 | private hostname 해석 여부 조사 필요 | private hostname 해석 여부 조사 필요 | 조사 필요 | 실행 호스트 | `nslookup`/접속 시험 | LLM, Git, Nexus, DB 주소 포함 |
| 방화벽 허용 목록 | 공개 서비스 접근 가능 가정 | 인바운드·아웃바운드 허용 대상 조사 필요 | 인바운드·아웃바운드 허용 대상 조사 필요 | 조사 필요 | 전체 연동 지점 | 네트워크 담당자 | 포트, 프로토콜, 목적지 FQDN 기준 정리 |
| 사내 CA | 시스템 기본 CA | 설치·갱신 방식 조사 필요 | 설치·갱신 방식 조사 필요 | 조사 필요 | Python, Git, Maven, Trivy | 인프라 담당자 | 각 도구별 trust store가 다를 수 있음 |
| 망분리·파일 반입 | 별도 제약 없음 | 반입·반출 절차 조사 필요 | 반입·반출 절차 조사 필요 | 조사 필요 | 소스 ZIP, 결과 ZIP, 캐시 | 보안 담당자 | 소스코드와 분석 결과의 보안등급 포함 |

### 4.4 Maven·Nexus·OpenRewrite

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| Maven 저장소 | Maven Central 또는 대상 POM의 저장소 사용 | Nexus/Artifactory 주소와 mirror 정책 조사 필요 | Nexus/Artifactory 주소와 mirror 정책 조사 필요 | 조사 필요 | `backend/app/mvnrewrite/mvn_settings.py` | 저장소 담당자 | 현재 public mirror 기능은 사외 fallback 용도 |
| 저장소 인증 | 로컬 Maven 설정에 의존 | 서비스 계정과 `settings.xml` 제공 방식 조사 필요 | 서비스 계정과 `settings.xml` 제공 방식 조사 필요 | 조사 필요 | Maven 실행 환경 | 저장소 담당자 | 비밀번호를 작업 산출물에 복사하지 않도록 주의 |
| OpenRewrite 플러그인·레시피 | Maven 저장소에서 동적 다운로드 | 필요한 버전의 사내 저장소 존재 여부 조사 필요 | 필요한 버전의 사내 저장소 존재 여부 조사 필요 | 조사 필요 | `backend/app/mvnrewrite/recipe_catalog.yaml` | 아티팩트 조회 시험 | offline 반입 시 전체 전이 의존성 필요 |
| Maven 플러그인 | Versions, Dependency-Check 등 다운로드 | 허용 및 캐시 여부 조사 필요 | 허용 및 캐시 여부 조사 필요 | 조사 필요 | `backend/app/mvnrewrite/`, `backend/app/scan/` | 실제 참고 프로젝트 실행 | 플러그인 prefix와 버전 고정 정책 확인 |
| JDK·Maven 버전 | PATH에서 탐색 | 표준 배포 버전 조사 필요 | 표준 배포 버전 조사 필요 | 조사 필요 | `backend/app/prereqs.py` | 사전 점검 스크립트 실행 | 목표 JDK 21 및 대상 프로젝트 호환성 확인 |
| 대상 사내 라이브러리 | 사외에서 접근 불가할 수 있음 | POSCO 전용 artifact 접근 확인 | POSCODX 전용 artifact 접근 확인 | 조사 필요 | 입력 프로젝트의 `pom.xml` | 대표 프로젝트 빌드 | 두 환경의 저장소 콘텐츠 차이 기록 필요 |

### 4.5 취약점 DB·Trivy·NVD

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| Trivy 실행 파일 | PATH의 `trivy` 사용 | 배포 또는 승인 버전 조사 필요 | 배포 또는 승인 버전 조사 필요 | 조사 필요 | `backend/app/prereqs.py`, `backend/app/scan/trivy.py` | 사전 점검 실행 | 바이너리 반입·업데이트 절차 포함 |
| Trivy DB·Java DB | 명시적 캐시 갱신 후 오프라인 스캔 | 온라인 갱신 또는 파일 반입 방식 조사 필요 | 온라인 갱신 또는 파일 반입 방식 조사 필요 | 조사 필요 | `backend/app/orchestration/cache_refresh.py` | 캐시 갱신 시험 | 빈 캐시는 취약점 미검출로 이어질 수 있음 |
| NVD 데이터 | Dependency-Check `update-only`로 갱신 | NVD 접근·미러·반입 방식 조사 필요 | NVD 접근·미러·반입 방식 조사 필요 | 조사 필요 | `backend/app/scan/dependency_check.py` | 캐시 전체 갱신 시험 | 최초 전체 갱신 시간과 용량 측정 필요 |
| NVD API Key | `NVD_API_KEY` 선택 설정 | 키 발급 가능 여부 조사 필요 | 키 발급 가능 여부 조사 필요 | 조사 필요 | `backend/app/config.py` | 보안·운영 담당자 | 키 없이 갱신할 때 rate limit 확인 |
| 캐시 공유·보존 | 로컬 디렉터리 사용 | 공유 볼륨과 갱신 주체 조사 필요 | 공유 볼륨과 갱신 주체 조사 필요 | 조사 필요 | `DEPENDENCY_CHECK_DATA_DIR`, `TRIVY_CACHE_DIR` | 배포 설계 확인 | 다중 인스턴스 동시 갱신 잠금 필요 여부 확인 |

### 4.6 Git과 입력 소스

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| Git 서버 | URL clone 또는 ZIP 업로드 | Git 서버 종류·주소·인증 조사 필요 | Git 서버 종류·주소·인증 조사 필요 | 조사 필요 | `backend/app/ingest/git_source.py` | 대표 private 저장소 clone | HTTPS, SSH, SSO 지원 범위 확인 |
| Git 자격증명 | `GIT_TOKEN`, SSH key 설정 필드 존재 | 토큰·SSH key 발급 정책 조사 필요 | 토큰·SSH key 발급 정책 조사 필요 | 조사 필요 | `backend/app/config.py` | Git 운영 담당자 | 현재 코드에서 각 필드의 실제 사용 여부 확인 필요 |
| Git 서버 인증서 | OS/Git 기본 trust store | 사내 CA 적용 방식 조사 필요 | 사내 CA 적용 방식 조사 필요 | 조사 필요 | Git 실행 환경 | clone 시험 | `sslVerify=false` 사용 금지 원칙 유지 |
| 소스 보관 | job 디렉터리에 source/work/output 저장 | 보관 기간과 삭제 정책 조사 필요 | 보관 기간과 삭제 정책 조사 필요 | 조사 필요 | `JOBS_DATA_DIR` | 정보보호 정책 확인 | 업로드 ZIP과 변환 소스 모두 포함 |

### 4.7 Python 패키지와 실행 환경

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| Python 버전 | Python 3.11 이상 | 표준 버전과 패치 정책 조사 필요 | 표준 버전과 패치 정책 조사 필요 | 조사 필요 | `backend/pyproject.toml` | 실행 호스트 확인 | OS별 subprocess 차이도 검증 |
| PyPI 접근 | 공개 PyPI에서 설치 가능 가정 | 사내 PyPI mirror 조사 필요 | 사내 PyPI mirror 조사 필요 | 조사 필요 | `backend/pyproject.toml` | 새 환경 설치 시험 | wheel 미제공 패키지의 빌드 도구 확인 |
| 의존성 버전 | 대부분 하한만 지정 | lock 및 승인 버전 정책 조사 필요 | lock 및 승인 버전 정책 조사 필요 | 조사 필요 | `backend/pyproject.toml` | 재현 설치 시험 | 환경별 설치 결과가 달라질 위험 있음 |
| OS·컨테이너 | 개발자 PC 로컬 실행 | Windows/Linux/컨테이너 여부 조사 필요 | Windows/Linux/컨테이너 여부 조사 필요 | 조사 필요 | 배포 환경 | 운영 방식 확인 | Java, Maven, Git, Trivy를 함께 제공해야 함 |

### 4.8 서버·프론트엔드·접근 제어

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| 백엔드 주소 | 기본 `127.0.0.1:8000` | 서비스 URL과 노출 범위 조사 필요 | 서비스 URL과 노출 범위 조사 필요 | 조사 필요 | `backend/app/config.py` | 배포 담당자 | reverse proxy 경유 여부 포함 |
| 프론트 API 주소 | 기본 `http://127.0.0.1:8000`, UI에서 변경 | 환경별 배포 설정 방식 조사 필요 | 환경별 배포 설정 방식 조사 필요 | 조사 필요 | `frontend/assets/common.js` | 브라우저 연동 시험 | 운영에서 사용자 직접 입력을 유지할지 결정 필요 |
| CORS | `CORS_ALLOW_ORIGINS` 문자열 목록 | 실제 프론트 origin 조사 필요 | 실제 프론트 origin 조사 필요 | 조사 필요 | `backend/app/main.py`, `config.py` | 브라우저 호출 시험 | wildcard보다 명시적 origin 권장 |
| HTTPS | 개발 환경 HTTP 가능 | TLS 종료 위치 조사 필요 | TLS 종료 위치 조사 필요 | 조사 필요 | reverse proxy 또는 앱 서버 | 보안·인프라 담당자 | SSE 장시간 연결 설정 포함 |
| SSE | Job 진행 이벤트에 사용 | proxy timeout/buffering 설정 조사 필요 | proxy timeout/buffering 설정 조사 필요 | 조사 필요 | `backend/app/streaming/`, 프론트 JS | 장시간 job 시험 | 중간 프록시가 응답을 버퍼링하면 실시간성이 깨짐 |
| 동시 사용자 | 기본 동시 repo 3개 | 자원·큐 정책 조사 필요 | 자원·큐 정책 조사 필요 | 조사 필요 | `MAX_CONCURRENT_REPOS` | 부하 시험 | Maven/LLM/스캔이 CPU·메모리·디스크를 함께 사용 |

### 4.9 DB·파일 저장·백업

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| Job DB | 로컬 SQLite | SQLite 허용 또는 중앙 DB 필요 여부 조사 | SQLite 허용 또는 중앙 DB 필요 여부 조사 | 조사 필요 | `DATABASE_URL`, `backend/app/models/db.py` | 배포 구조 검토 | 다중 인스턴스라면 공유 DB 검토 필요 |
| 작업 파일 | 로컬 `JOBS_DATA_DIR` | 저장 경로·용량·암호화 조사 필요 | 저장 경로·용량·암호화 조사 필요 | 조사 필요 | `backend/app/config.py` | 스토리지 담당자 | 소스와 AI 로그가 포함됨 |
| 보존·삭제 | 사용자 삭제 API 중심 | 자동 보존 기간 조사 필요 | 자동 보존 기간 조사 필요 | 조사 필요 | Jobs API, job 디렉터리 | 정보보호 담당자 | DB 행과 파일의 일관된 삭제 검증 필요 |
| 백업·복구 | 별도 정책 없음 | 백업 대상과 복구 목표 조사 필요 | 백업 대상과 복구 목표 조사 필요 | 조사 필요 | DB, job output, 캐시 | 운영 담당자 | 재생성 가능한 캐시는 백업 제외 가능 |
| 디스크 한도 | 업로드 크기·파일 수 제한 존재 | 전체 job 용량·동시 job 한도 조사 필요 | 전체 job 용량·동시 job 한도 조사 필요 | 일부 확인 | `backend/app/config.py` | 대용량 프로젝트 시험 | Maven 캐시와 스캔 DB 공간도 별도 고려 |

### 4.10 관측성·로그·정보보호

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| LangSmith | 설정 시 외부 endpoint로 trace 전송 | 사용 허용 또는 사내 대체 endpoint 조사 필요 | 사용 허용 또는 사내 대체 endpoint 조사 필요 | 조사 필요 | `configure_langsmith_env()`, `LANGSMITH_*` | 보안·LLM 플랫폼 담당자 | 소스·프롬프트·응답의 외부 반출 가능성 검토 |
| 로컬 LLM 로그 | job output에 JSON 기록 | 보관·열람·마스킹 정책 조사 필요 | 보관·열람·마스킹 정책 조사 필요 | 조사 필요 | `backend/app/orchestration/callbacks.py` | 샘플 로그 내용 점검 | 소스코드 일부와 모델 응답이 포함될 수 있음 |
| 애플리케이션 로그 | 로컬 로깅 | 중앙 로그 연동 방식 조사 필요 | 중앙 로그 연동 방식 조사 필요 | 조사 필요 | `backend/app/logging_conf.py` | 운영 플랫폼 확인 | job ID 기반 추적 가능 여부 확인 |
| 감사 로그 | 별도 사용자 감사 체계 없음 | 사용자·작업·다운로드 감사 요건 조사 필요 | 사용자·작업·다운로드 감사 요건 조사 필요 | 조사 필요 | API 계층 | 보안 담당자 | 단일 API 토큰으로는 사용자 구분이 어려움 |
| 소스의 LLM 전송 | AI 수정 시 필요한 파일 내용과 빌드 오류를 모델에 제공 | 허용 범위와 비식별화 기준 조사 필요 | 허용 범위와 비식별화 기준 조사 필요 | 조사 필요 | Stage 1·2 agent | 정보보호 승인 | private LLM이라도 데이터 보존·학습 사용 정책 확인 |

### 4.11 운영·장애 처리

| 검토 항목 | 현재 사외 환경 | POSCO 환경 | POSCODX 환경 | 상태 | 관련 위치 | 확인 방법·담당 | 비고 |
|---|---|---|---|---|---|---|---|
| 운영 주체 | 개발자 로컬 실행 | 설치·운영·문의 담당 조사 필요 | 설치·운영·문의 담당 조사 필요 | 조사 필요 | 운영 절차 | 조직 간 역할 확인 | 환경별 담당 조직과 연락 경로 기록 |
| Health check | `GET /health`, 사전조건 API 제공 | 모니터링 연동 기준 조사 필요 | 모니터링 연동 기준 조사 필요 | 조사 필요 | API router | 모니터링 담당자 | LLM·Nexus 등 downstream 상태 포함 여부 결정 |
| 장애 메시지 | job 실패 로그와 상태 제공 | 사용자 공개 범위와 운영 알림 조사 필요 | 사용자 공개 범위와 운영 알림 조사 필요 | 조사 필요 | orchestration, frontend | 장애 시나리오 시험 | 내부 hostname·경로·Secret 노출 방지 |
| 업데이트 | 수동 코드·의존성 갱신 | 배포 승인·롤백 절차 조사 필요 | 배포 승인·롤백 절차 조사 필요 | 조사 필요 | 저장소·배포 파이프라인 | 운영 담당자 | Python, JDK, Maven, Trivy, DB 스키마 포함 |

## 5. 사내 반입 검증 시나리오

환경마다 아래 시나리오를 독립적으로 수행하고 결과와 확인 일자를 기록한다.

1. Python 의존성을 승인된 저장소만 사용하여 신규 설치한다.
2. Java, Maven, Git, Trivy 사전조건 검사를 통과한다.
3. 해당 환경의 private Git 저장소를 clone하고 ZIP 업로드도 수행한다.
4. 대표 Maven 단일·멀티모듈 프로젝트에서 dependency resolve와 compile을 수행한다.
5. OpenRewrite 레시피와 Maven Versions Plugin을 실행한다.
6. 해당 환경의 LLM `base_url`로 최소 채팅 호출을 수행한다.
7. 실제 Stage 1 agent를 실행해 tool calling과 응답 파싱을 검증한다.
8. LLM 인증 오류, timeout, 429, 5xx 상황의 사용자 메시지와 로그를 확인한다.
9. Trivy와 Dependency-Check 캐시를 준비하고 알려진 취약점이 있는 샘플을 탐지한다.
10. 프론트엔드에서 REST, 파일 업로드, SSE 진행 표시, 결과 다운로드를 검증한다.
11. job 취소·삭제 후 subprocess, DB 행, 작업 파일이 의도대로 정리되는지 확인한다.
12. 로그와 산출물에 Secret이나 허용되지 않은 민감정보가 남지 않는지 점검한다.

## 6. 우선 조사 질문

### 6.1 POSCO 환경

1. LLM Gateway의 API 호환 규격, 모델 식별자, 인증 방식, Secret 발급 절차는 무엇인가?
2. `ChatOpenAI`에 전달해야 하는 필수·금지 `kwargs`는 무엇인가?
3. LLM의 tool calling, 스트리밍, 최대 context/output token을 지원하는가?
4. 사내 CA, 프록시, DNS, 방화벽 허용 목록은 어떻게 적용하는가?
5. Python 및 Maven 패키지 저장소 주소와 서비스 계정 정책은 무엇인가?
6. NVD·Trivy DB를 온라인 갱신할 수 있는가, 아니면 정기 반입해야 하는가?
7. 소스코드, 프롬프트, 모델 응답, 빌드 로그의 저장·전송·보존 정책은 무엇인가?
8. 실행 형태는 개발자 PC, 공용 서버, VM, 컨테이너 중 무엇인가?

### 6.2 POSCODX 환경

1. LLM Gateway의 API 호환 규격, 모델 식별자, 인증 방식, Secret 발급 절차는 무엇인가?
2. `ChatOpenAI`에 전달해야 하는 필수·금지 `kwargs`는 무엇인가?
3. LLM의 tool calling, 스트리밍, 최대 context/output token을 지원하는가?
4. 사내 CA, 프록시, DNS, 방화벽 허용 목록은 어떻게 적용하는가?
5. Python 및 Maven 패키지 저장소 주소와 서비스 계정 정책은 무엇인가?
6. NVD·Trivy DB를 온라인 갱신할 수 있는가, 아니면 정기 반입해야 하는가?
7. 소스코드, 프롬프트, 모델 응답, 빌드 로그의 저장·전송·보존 정책은 무엇인가?
8. 실행 형태는 개발자 PC, 공용 서버, VM, 컨테이너 중 무엇인가?

## 7. 변경 이력

| 일자 | 변경 내용 |
|---|---|
| 2026-08-10 | 최초 작성. POSCO/POSCODX 환경 구분, LLM `base_url` 차이와 초기 조사 체크리스트 반영 |
