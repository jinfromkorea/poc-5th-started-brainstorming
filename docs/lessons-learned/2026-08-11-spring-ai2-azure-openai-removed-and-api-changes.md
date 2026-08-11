# Spring AI 2.0 마이그레이션 — Azure OpenAI 스타터 제거 + API 변경 3종 + 자동 검증을 통과한 가짜 성공

## 메타데이터

- 날짜: 2026-08-11
- 근거: job #44 (`backend/data/jobs/44`), Stage 1 "Spring AI 1.1.8 -> 2.0" 스텝은 도구 안에서 `success`로 끝났지만, 그 뒤 사람이 직접 `mvn compile`을 돌려보니 `ace-ai` 모듈에서 컴파일 에러 발견
- 적용 대상: Spring AI **1.1.x → 2.0.x**로 올라가는 마이그레이션
- 카테고리: 마이그레이션 실패 사례 / 라이브러리 기능 완전 제거(단순 리네임 아님) / Spring AI 2.0 API 변경 / **자동 검증(mvn test-compile)이 실제로는 아무것도 안 하고 통과 처리한 사례**
- 키워드: `Spring AI 2.0`, `Azure OpenAI`, `AzureOpenAIClientBuilderCustomizer`, `OpenAiHttpClientBuilderCustomizer`, `spring-ai-starter-model-azure-openai`, `ModelOptionsUtils`, `toJsonStringPrettyPrinter`, `ChatOptions.Builder`, `ChatClient.Builder#defaultOptions`, `mutate()`, `Nothing to compile - all classes are up to date`, incremental compile, false positive

## 증상 (원본 에러 메시지)

`io.arconia.rewrite.spring.ai2.UpgradeSpringAi_2_0` 레시피가 적용된 직후, 도구의 자체 검증(`mvn test-compile`)은 **통과**했고 체크포인트도 커밋됐다. 그런데 그 job이 이미 `success`로 끝난 뒤, 사람이 같은 `work/`에서 직접 `mvn compile`을 돌리자 바로 실패했다:

```
[ERROR] .../ace-ai/src/main/java/com/poscodx/ai/ace/config/AzureOpenAiConfig.java:[3,27] package com.azure.core.http does not exist
[ERROR] .../ace-ai/src/main/java/com/poscodx/ai/ace/config/AzureOpenAiConfig.java:[4,27] package com.azure.core.util does not exist
[ERROR] .../ace-ai/src/main/java/com/poscodx/ai/ace/config/AzureOpenAiConfig.java:[5,63] package org.springframework.ai.model.azure.openai.autoconfigure does not exist
[ERROR] .../ace-ai/src/main/java/com/poscodx/ai/ace/config/AzureOpenAiConfig.java:[25,12] cannot find symbol
  symbol:   class AzureOpenAIClientBuilderCustomizer
```

이 파일을 고친 뒤 다시 빌드하니 **같은 모듈에서 에러 2개가 더** 나왔다(전부 같은 레시피 한 번의 결과):

```
[ERROR] .../component/AdvisorFactory.java:[81,32] invalid method reference
  cannot find symbol
    symbol:   method toJsonStringPrettyPrinter(org.springframework.ai.chat.model.ChatResponse)
    location: class org.springframework.ai.model.ModelOptionsUtils
[ERROR] .../component/ChatClientFactory.java:[75,66] incompatible types: org.springframework.ai.chat.prompt.ChatOptions cannot be converted to org.springframework.ai.chat.prompt.ChatOptions.Builder
[ERROR] .../component/ChatClientFactory.java:[103,47] incompatible types: org.springframework.ai.chat.prompt.ChatOptions cannot be converted to org.springframework.ai.chat.prompt.ChatOptions.Builder
```

## ⚠️ 가장 중요한 발견 — 자동 검증이 "통과"라고 말한 건 거짓말이었다

레시피 적용 직후 도구가 실제로 남긴 `mvn test-compile` 로그(`output/logs/stage1/*-mvn-test-compile.log`)를 열어보면, **`ace-ai` 모듈을 포함한 모든 모듈이 이렇게 나온다**:

```
[INFO] --- compiler:3.15.0:compile (default-compile) @ ace-ai ---
[INFO] Nothing to compile - all classes are up to date.
```

즉 javac가 `AzureOpenAiConfig.java`를 **한 번도 다시 컴파일하지 않았다.** 이유: `UpgradeSpringAi_2_0` 레시피는 `ace-ai/pom.xml`에서 `spring-ai-starter-model-azure-openai` 의존성 선언만 지웠을 뿐, 그 의존성을 쓰던 `.java` 소스 파일은 단 한 글자도 안 건드렸다. Maven의 증분 컴파일(incremental compile)은 기본적으로 **소스 파일의 mtime이 클래스 파일보다 최신인지**만 보고 재컴파일 여부를 판단하는데, `pom.xml`의 의존성 목록이 바뀐 것은 이 검사에 안 걸린다. 그래서 이전(Spring AI 1.1.8 시절)에 이미 성공적으로 컴파일해둔 `target/classes/.../AzureOpenAiConfig.class`가 "최신"이라고 판단되어 재컴파일을 건너뛰었고, 검증은 아무것도 검증하지 않은 채 통과했다.

**이건 job #44의 Jackson 사례(레슨런드: `2026-08-11-jackson3-objectmapper-migration.md`)의 "Lombok cascading error 오진단"과는 완전히 다른, 더 근본적인 문제다.** 그때는 에러가 있긴 있었는데 잘못된 파일을 가리켰을 뿐이지만, 이번엔 **에러가 있는데도 빌드가 "성공"으로 보고됐다.**

→ **일반화 가능한 규칙**: OpenRewrite 레시피(또는 AI)가 `pom.xml`의 의존성만 지우거나 바꾸고 그 의존성을 참조하는 `.java` 소스는 안 건드리는 패턴이면, 직후의 `mvn test-compile`(증분 컴파일)이 실제로는 아무것도 다시 안 보고 통과를 보고할 수 있다. 검증 로그에서 해당 모듈에 `Nothing to compile - all classes are up to date.`만 찍혀 있다면 그 검증은 신뢰할 수 없다 — 의존성이 바뀐 스텝 뒤의 검증은 `mvn clean test-compile`처럼 증분 캐시를 무시하는 방식으로 돌아야 진짜로 검증된다.

## 진짜 원인 3가지 — Spring AI 2.0의 실제 API 변경 (전부 jar를 직접 까서 확인)

### 1. Azure OpenAI 챗 모델 스타터가 2.0에서 완전히 없어졌다 (단순 이동이 아님)

로컬 `.m2`에 `spring-ai-starter-model-azure-openai`는 1.1.8만 있고 2.0은 없었다. Maven Central에 직접 `spring-ai-starter-model-azure-openai:2.0.0`을 요청해보니 **존재하지 않음**(다운로드 실패로 확인). `spring-ai-bom:2.0.0`을 열어봐도 Anthropic/Bedrock/Ollama/Mistral/Google GenAI/DeepSeek 등 다른 프로바이더 스타터는 다 있는데 **Azure OpenAI "챗 모델" 스타터만 없다**(Azure "벡터 스토어" 스타터인 `spring-ai-starter-vector-store-azure`는 남아 있음 — 별개 기능이라 안 지워짐).

**고치는 법**: Spring AI 2.0에서 Azure OpenAI는 이제 전용 클라이언트가 아니라 **범용 OpenAI 호환 클라이언트**(`spring-ai-starter-model-openai`, 이미 대부분 프로젝트가 갖고 있음)를 Azure 리소스의 OpenAI 호환 엔드포인트(`spring.ai.openai.base-url`)로 지정하는 방식으로 접근한다. 그 클라이언트의 HTTP 빌더를 커스터마이즈하려면 새 확장점 `org.springframework.ai.openai.http.okhttp.OpenAiHttpClientBuilderCustomizer`(패키지: `spring-ai-openai`, 이미 `spring-ai-starter-model-openai`의 전이 의존성)를 쓴다:

```java
// Before (Jar 자체가 더 이상 안 받아짐)
import com.azure.core.http.HttpClient;
import com.azure.core.util.HttpClientOptions;
import org.springframework.ai.model.azure.openai.autoconfigure.AzureOpenAIClientBuilderCustomizer;
...
@Bean
public AzureOpenAIClientBuilderCustomizer responseTimeoutCustomizer() {
    return openAiClientBuilder -> {
        var clientOptions = new HttpClientOptions().setResponseTimeout( ofMinutes( 20 ) );
        openAiClientBuilder.httpClient( HttpClient.createDefault( clientOptions ) );
    };
}

// After
import org.springframework.ai.openai.http.okhttp.OpenAiHttpClientBuilderCustomizer;
...
@Bean
public OpenAiHttpClientBuilderCustomizer responseTimeoutCustomizer() {
    return builder -> builder.timeout( ofMinutes( 20 ) );
}
```

`@ConditionalOnProperty(name = "spring.ai.model.chat", havingValue = "azure-openai")` 가드는 그대로 유지 — 다운스트림 프로젝트가 이 프로퍼티로 azure 모드를 켤 수 있다는 계약 자체는 안 바뀌었고, 바뀐 건 그 모드에서 실제로 커스터마이즈하는 대상(클라이언트 구현체)뿐이다.

### 2. `ModelOptionsUtils`가 메서드 하나만 남기고 전부 삭제됨

1.1.8엔 `toJsonString`/`toJsonStringPrettyPrinter`/`jsonToMap`/`merge`/`objectToMap`/`mapToClass`/`getJsonSchema` 등이 다 있었는데, 2.0엔 `mergeOption` 하나만 남았다(`javap`로 클래스 구조 직접 비교해 확인).

**고치는 법**: 프로젝트에 이미 있는 JSON 유틸을 재사용한다(`ace-common`의 `JacksonUtil.writeToJsonWithPretty(Object)` — Jackson 3용으로 이미 고쳐둔 것, 레슨런드 `2026-08-11-jackson3-objectmapper-migration.md` 참고). 새 의존성 불필요.

```java
// Before
responseToString = ModelOptionsUtils::toJsonStringPrettyPrinter;
// After
responseToString = JacksonUtil::writeToJsonWithPretty;
```

### 3. `ChatClient.Builder#defaultOptions`가 `ChatOptions` 대신 `ChatOptions.Builder`를 받는다

`javap`로 확인: `defaultOptions(ChatOptions.Builder)`로 시그니처가 바뀌었다. `ChatOptions` 인터페이스엔 새로 `mutate()`(현재 값으로 채운 `Builder<?>` 반환)와 정적 `builder()`가 생겼다.

```java
// Before
return baseChatClient.mutate()
                     .defaultOptions( chatOptions )
                     .defaultAdvisors( advisors )
                     .build();
// After
return baseChatClient.mutate()
                     .defaultOptions( chatOptions.mutate() )
                     .defaultAdvisors( advisors )
                     .build();
```

이미 만들어둔 `ChatOptions` 인스턴스를 넘기는 모든 호출부에서 `.mutate()`를 붙이면 된다 — `getChatOptions(String)`처럼 `ChatOptions`를 리턴하는 public 메서드의 반환 타입 자체는 안 바꿔도 된다(다른 호출자들과의 계약을 안 깨는 게 더 안전).

## 검증

```bash
mvn -B compile        # 전체 리액터(ace-parent/ace-common/ace-ai/ace-util), exit 0
mvn -B test-compile   # 동일, exit 0
```

세 파일(`AzureOpenAiConfig.java`, `AdvisorFactory.java`, `ChatClientFactory.java`)만 고쳤고 나머지 3개 모듈은 그대로 통과.

## 일반화 가능한 규칙 (다음 마이그레이션에 재사용)

1. **Spring AI 1.x → 2.0 마이그레이션에서 Azure OpenAI 챗 모델(`spring-ai-starter-model-azure-openai`)을 쓰는 프로젝트는 전부 깨진다** — 단순 API 이동이 아니라 아티팩트 자체가 없어졌다. `spring-ai-starter-model-openai` + `OpenAiHttpClientBuilderCustomizer` 조합으로 재설계해야 한다.
2. **`ModelOptionsUtils`의 JSON 유틸 메서드(`toJsonString*`, `jsonToMap`, `merge`, `objectToMap` 등)를 쓰는 코드는 전부 깨진다** — `mergeOption` 말고는 다 삭제됐다. 프로젝트에 이미 Jackson 유틸이 있다면 그걸 재사용.
3. **`ChatClient.Builder#defaultOptions(ChatOptions)`를 직접 호출하는 코드는 전부 깨진다** — `.mutate()`를 붙여 `Builder`로 바꿔줘야 한다.
4. **가장 중요**: 레시피/AI가 `pom.xml`의 의존성만 바꾸고 그 의존성을 참조하는 `.java` 소스는 안 건드리는 패턴이 나오면, 직후 검증(`mvn test-compile`)의 로그에서 영향받은 모듈에 `Nothing to compile - all classes are up to date.`만 찍혀 있는지 반드시 확인할 것. 그렇다면 그 검증은 실제로 아무것도 검증하지 않은 것이다 — 의존성이 바뀐 스텝 뒤에는 증분 캐시를 무시하는 빌드(`mvn clean test-compile` 등)로 다시 확인해야 한다. **자동 파이프라인이 "성공"이라고 보고한 job이라도, 실제 배포/사용 전에 한 번은 `mvn clean compile`(또는 `clean test-compile`)로 직접 재확인하는 습관이 필요하다.**

## 참고 — 도구 자체의 개선 여지 (범위 밖, 이번엔 안 고침)

이번 사례는 `graph_stage1.py`의 verify 노드가 매번 증분 `mvn test-compile`만 돌리기 때문에 생겼다. 의존성 변경이 있었던 스텝(레시피가 `pom.xml`을 건드린 경우) 뒤에는 최소한 영향받은 모듈만이라도 `mvn clean`을 먼저 돌리거나, 아예 검증을 `mvn clean test-compile`로 바꾸는 게 이런 거짓 성공을 근본적으로 막는 방법일 것 — 다만 매 스텝마다 clean이 들어가면 검증 시간이 늘어나는 트레이드오프가 있어 별도 설계 논의가 필요하다.

## 관련 파일

- 고친 파일(전부 job 데이터, 이 도구의 소스 저장소 밖 — 별도 per-job git 저장소): `backend/data/jobs/44/work/ace-ai/src/main/java/com/poscodx/ai/ace/config/AzureOpenAiConfig.java`, `.../component/AdvisorFactory.java`, `.../component/ChatClientFactory.java`
- 거짓 통과를 보여준 로그: `backend/data/jobs/44/output/logs/stage1/1786452440123-mvn-test-compile.log`
- 관련 카탈로그 항목: `backend/app/mvnrewrite/recipe_catalog.yaml`의 `spring_ai_steps` 중 `to: "2.0"`(서드파티 레시피 `io.arconia.rewrite.spring.ai2.UpgradeSpringAi_2_0`)
