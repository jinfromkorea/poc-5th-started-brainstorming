# Maven Stack Upgrade Tool — Frontend

백엔드([`../backend`](../backend))의 REST/SSE API만 호출하는 순수 정적 HTML 클라이언트입니다. 빌드 단계가 없습니다 — 백엔드와 독립적으로 배포/수정할 수 있도록 일부러 이렇게 만들었습니다.

## 실행

정적 파일을 아무 HTTP 서버로 띄우면 됩니다. `file://`로 직접 여는 것은 피하세요 — 브라우저가 `fetch`/`EventSource`의 CORS 처리를 오리진이 `null`인 경우 예측 불가능하게 다룹니다.

```bash
cd frontend
python -m http.server 5500
# http://127.0.0.1:5500 접속
```

(VS Code의 Live Server 확장을 써도 됩니다 — 기본 포트도 5500입니다.)

백엔드의 `CORS_ALLOW_ORIGINS`(`backend/.env`)가 이 프론트엔드를 띄운 오리진과 일치해야 합니다. 기본값은 `http://localhost:5500`입니다. 다른 포트/호스트로 띄웠다면 `backend/.env`의 `CORS_ALLOW_ORIGINS`를 맞춰 바꾸고 백엔드를 재시작하세요.

페이지를 열면 상단 "연결 설정"에서:
- **API 서버 주소** — 기본 `http://127.0.0.1:8000`
- **API 토큰** — 백엔드 `.env`의 `API_AUTH_TOKEN`을 채워둔 경우에만 입력 (비어 있으면 인증 없이 요청)

두 값 모두 브라우저 `localStorage`에 저장되어 새로고침 후에도 유지됩니다.

## 화면 흐름

1. Git URL 또는 ZIP 파일 중 하나를 선택해 소스를 지정하고, 필요하면 출력 아티팩트 버전과 1/2단계 실행 여부를 선택한 뒤 "작업 시작"
2. `POST /jobs` 202 응답으로 받은 `job_id`로 `GET /jobs/{id}/events`(SSE)에 연결해 진행 로그와 상태를 실시간으로 표시
3. 작업이 종료 상태(success / needs_handoff / failed)에 도달하면 `GET /jobs/{id}/artifacts`로 결과물 목록을 조회해 diff / report / (있다면) AI 인수인계 가이드 버튼을 표시
4. 각 결과물은 복사 또는 다운로드 가능

## 수동 스모크 테스트 체크리스트

자동 UI 테스트 프레임워크는 두지 않았습니다(정적 HTML + vanilla JS, 빌드 없음). 백엔드를 바꾼 뒤에는 아래를 수동으로 확인하세요:

- [ ] 페이지 로드 시 API 서버 주소 기본값이 채워지고, 이전에 입력한 토큰이 있으면 복원되는지
- [ ] Git URL / ZIP 업로드 라디오 전환 시 해당 입력 필드만 보이는지
- [ ] 둘 다 비운 채 제출 시 폼 유효성 에러 메시지가 뜨는지 (요청이 나가지 않아야 함)
- [ ] ZIP 업로드로 유효한 Maven 프로젝트 제출 → 진행 패널에 로그가 실시간으로 쌓이고 상태 배지가 `queued` → `running` → 종료 상태로 바뀌는지
- [ ] 종료 후 결과물 패널에 diff/report 버튼이 활성화되고, 클릭 시 내용이 보이는지 + 복사/다운로드가 동작하는지
- [ ] Gradle 프로젝트(= `pom.xml` 없음) 제출 시 상태가 `failed`로 끝나고 에러 로그가 표시되는지
- [ ] `API_AUTH_TOKEN`을 설정한 백엔드에 잘못된 토큰으로 접근 시 진행 패널에 인증 오류가 표시되는지 (SSE는 쿼리 파라미터로 토큰을 전달합니다 — `EventSource`는 커스텀 헤더를 지원하지 않기 때문)
