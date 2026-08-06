# ACE Upgrade Tool

사내 Maven 시스템을 **Java 21 / Spring Boot 4.1 / Spring Cloud 2025.1 / Spring AI 2.0**으로 마이그레이션하고, 남은 개별 OSS 취약점을 패치하는 도구입니다.

- 설계 스펙: [`docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md`](docs/superpowers/specs/2026-08-06-oss-dependency-governance-design.md)
- 백엔드: [`backend/`](backend/README.md) (Python/FastAPI + LangChain/LangGraph)
- 프론트엔드: [`frontend/`](frontend/README.md) (정적 HTML, 백엔드와 별도 배포)

이 도구는 중앙 서버가 아니라 **각 시스템 유지보수 담당자(개발자)가 자기 PC에서 직접 실행**하는 것을 전제로 합니다. 시작하려면 [`backend/README.md`](backend/README.md)의 "사전 준비 확인"부터 따라가세요.

## 폴더 구조

```
backend/    # FastAPI 백엔드 (이 도구의 실제 구현)
frontend/   # 정적 HTML 프론트엔드
data/       # 참고/테스트용 사내 Maven 저장소 ZIP (도구가 다루는 소스 예시 — 도구 자체 코드 아님)
draft/      # 브레인스토밍 초안 (a.md: 인입 파이프라인 다이어그램, .env: 설정 템플릿 원본)
docs/       # 설계 스펙
```
