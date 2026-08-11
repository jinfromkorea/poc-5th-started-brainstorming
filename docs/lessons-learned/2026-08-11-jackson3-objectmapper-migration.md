# Spring Boot 4 마이그레이션 — Jackson 2 → 3 전환 시 `ObjectMapper` 빌더/모듈 문제

## 메타데이터

- 날짜: 2026-08-11
- 근거: job #44 (`backend/data/jobs/44`), Stage 1 `Spring Boot 3.5 -> 4.0` 스텝에서 `needs_handoff`
- 적용 대상: Spring Boot **3.5.x → 4.0.x** 이상으로 올라가는 마이그레이션(= Jackson 2 → **Jackson 3**로 그룹ID가 바뀌는 지점, `com.fasterxml.jackson.*` → `tools.jackson.*`)
- 카테고리: 마이그레이션 실패 사례 / 오진단 주의 / Jackson 3 API 변경
- 키워드: `Jackson3`, `JsonMapper`, `JavaTimeModule`, `Jackson2ObjectMapperBuilder`, `DateTimeFeature`, `cannot find symbol`, `Lombok`, `cascading compile error`, `tools.jackson`, `@Slf4j`, `variable log`

## 증상 (원본 에러 메시지)

`org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_0` 레시피 적용 직후 `mvn test-compile`이 실패했다. 재시도(AI 수정) 2회 뒤 최종적으로 나온 에러 목록은 아래처럼 **한 파일과 무관해 보이는 클래스들**에서 대량으로 발생했다:

```
[ERROR] .../common/APIExceptionAdvice.java:[99,9] cannot find symbol
  symbol:   variable log
  location: class com.poscodx.ai.ace.common.APIExceptionAdvice
[ERROR] .../config/CorsConfig.java:[51,9] cannot find symbol
  symbol:   variable log
  location: class com.poscodx.ai.ace.config.CorsConfig
[ERROR] .../domain/dto/ModelInfo.java:[15,26] cannot find symbol
  symbol:   method getDisplayName()
  location: variable chatModel of type com.poscodx.ai.ace.domain.entity.ChatModel
[ERROR] .../auth/AuthenticatedUserDetails.java:[61,21] cannot find symbol
  symbol:   method getUsername()
  location: variable user of type com.poscodx.ai.ace.domain.entity.User
```

`log`(Lombok `@Slf4j`가 생성)와 `getXxx()`(Lombok `@Getter`가 생성)가 프로젝트 전역에서 갑자기 안 보이는 것처럼 나온다.

## ⚠️ 오진단 주의 — 이게 진짜 원인이 아니다

**"Lombok이 프로젝트 전체에서 깨졌다"로 보이지만 실제로는 아니다.** 이 심볼들은 전부 정상 클래스에 정상적으로 선언돼 있다(`@Slf4j`, `@Getter` 그대로 있음). AI가 이 가짜 단서를 따라가 `APIExceptionAdvice.java`, `JwtTokenValidator.java` 등 **관계없는 파일들을 고치려고 시도**했고, 그래도 안 고쳐졌다(당연하다 — 애초에 안 망가진 파일이므로).

**판별 방법**: 재시도가 쌓이기 전, **가장 처음 뜬 컴파일 에러 하나만** 확인한다.

```
[ERROR] .../util/JacksonUtil.java:[7,43] cannot find symbol
  symbol:   class JavaTimeModule
  location: package tools.jackson.databind.ext.javatime
[INFO] 1 error
```

최초 컴파일(AI가 아무것도 안 건드린 시점)에서는 에러가 **딱 1개**, `JacksonUtil.java`의 `JavaTimeModule` 하나뿐이었다. `log`/getter 무더기 에러는 AI가 이 파일을 (불완전하게) 고친 **두 번째 컴파일부터** 나타났다.

**메커니즘 (일반화 가능한 규칙)**: javac가 어노테이션 프로세싱(Lombok)을 라운드 단위로 처리하는데, 같은 컴파일 배치 안의 **한 파일에서 하드 컴파일 에러가 나면 그 라운드에서 다른(관계없는) 파일들의 Lombok 코드 생성이 같이 틀어질 수 있다.** 즉 "여러 파일에서 Lombok이 갑자기 다 깨졌다"는 신호는 대부분 **그 배치 안 어딘가에 있는 단 하나의 진짜 컴파일 에러의 부작용**이다.

→ **앞으로 이런 패턴(다수의 무관한 클래스에서 동시에 `log`/getter를 못 찾는다)을 보면, 그 여러 파일을 고치려 하지 말고 가장 처음(재시도 0회차) 컴파일 로그의 에러 목록부터 확인할 것.** 보통 진짜 원인은 1~2개의 특정 파일에 몰려 있다.

## 진짜 원인 — Jackson 2 → 3 API 변경 두 가지

`UpgradeSpringBoot_4_0` 레시피가 `JacksonUtil.java`의 import를 `com.fasterxml.jackson.*` → `tools.jackson.*`로 대부분 잘 바꿨지만, 아래 두 지점에서 Jackson 3의 실제 API와 어긋났다(둘 다 로컬 `.m2`의 실제 `jackson-databind-3.1.4.jar`를 `javap`로 까서 검증함).

### 1. `JavaTimeModule`이라는 클래스 자체가 없어졌다

```java
// Before (레시피가 만든, 컴파일 안 되는 코드)
import tools.jackson.databind.ext.javatime.JavaTimeModule;
...
Jackson2ObjectMapperBuilder.json()
    .modules( new JavaTimeModule() )
    .build();
```

Jackson 3는 java.time(JSR-310) 지원이 `jackson-databind` 코어에 기본 내장돼 있다 — 별도 모듈을 만들어 등록할 필요 자체가 없어졌다. (`tools/jackson/databind/ext/javatime/` 패키지 안에 서비스/직렬화 클래스들은 있지만, `JavaTimeModule.class`라는 이름의 클래스는 없음 — jar 안 파일 목록으로 직접 확인.)

**고치는 법**: import와 `.modules(new JavaTimeModule())` 호출을 그냥 삭제한다.

### 2. `Jackson2ObjectMapperBuilder`(Spring 클래스)가 Jackson 3 전용 클래스패스에서 아예 못 쓰인다

```java
// Before
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;
private static final ObjectMapper mapper = Jackson2ObjectMapperBuilder.json()
                                                                      .featuresToDisable( FAIL_ON_EMPTY_BEANS, WRITE_DATES_AS_TIMESTAMPS )
                                                                      .build();
```

에러:
```
[ERROR] JacksonUtil.java:[28,75] cannot access com.fasterxml.jackson.databind.ObjectMapper
  class file for com.fasterxml.jackson.databind.ObjectMapper not found
```

클래스 이름의 "2"가 문자 그대로 Jackson **2** 전용이라는 뜻이었다 — 내부적으로 `com.fasterxml.jackson.databind.ObjectMapper`(Jackson 2)를 참조하는데, Boot 4 + Jackson 3 클래스패스에는 Jackson 2 자체가 없어서 그 클래스 파일을 못 찾는다. deprecated-for-removal 경고도 이미 떠 있었다(`Jackson2ObjectMapperBuilder ... has been deprecated and marked for removal`).

**고치는 법**: Jackson 3 자체 빌더인 `tools.jackson.databind.json.JsonMapper.builder()`로 교체한다.

```java
// After
import tools.jackson.databind.json.JsonMapper;
private static final ObjectMapper mapper = JsonMapper.builder()
                                                      .disable( FAIL_ON_EMPTY_BEANS )
                                                      .disable( WRITE_DATES_AS_TIMESTAMPS )
                                                      .build();
```

주의: `MapperBuilder.disable(...)`는 `SerializationFeature`용과 `DatatypeFeature`(`DateTimeFeature`가 구현)용이 **서로 다른 오버로드**라, 한 번의 `.disable(A, B)` 호출에 섞어 넣을 수 없다 — 타입별로 따로 호출해야 한다.

### 참고: `WRITE_DATES_AS_TIMESTAMPS`의 원래 위치는 맞았다

레시피가 애초에 넣어둔 `import static tools.jackson.databind.cfg.DateTimeFeature.WRITE_DATES_AS_TIMESTAMPS;`는 **맞는 위치**였다(`DateTimeFeature` enum에 실제로 존재, `javap`로 확인). 그런데 AI가 재시도 중 이걸 `tools.jackson.databind.SerializationFeature.WRITE_DATES_AS_TIMESTAMPS`로 "고쳤는데" — `SerializationFeature`에는 그 멤버가 없다(마찬가지로 `javap`로 확인). **AI의 수정이 오히려 맞는 코드를 틀리게 만든 사례.** 레시피/기존 코드가 이미 맞는 부분까지 재시도 과정에서 건드리지 않도록 하는 게 중요하다는 방증이기도 하다.

## 검증

`JacksonUtil.java` 딱 한 파일만 위 두 가지로 고친 뒤:

```bash
mvn -B compile -q   # 전체 리액터(ace-parent/ace-common/ace-ai/ace-util)
echo $?             # -> 0
```

- `mvn compile`(전체 리액터) exit 0, 에러 없음.
- `log`/getter "cannot find symbol" 에러가 **전부** 사라짐(오진단이었다는 방증).
- 남은 문제는 `mvn test-compile`에서 발견된 **완전히 별개의** 이슈 하나뿐(아래 참고).

## 일반화 가능한 규칙 (다음 마이그레이션에 재사용)

1. **Spring Boot 3.5 → 4.0(Jackson 2 → 3) 마이그레이션에서 `Jackson2ObjectMapperBuilder`를 쓰는 코드는 전부 깨진다.** `JsonMapper.builder()`(또는 Spring이 제공하는 Jackson-3용 대체 빌더가 있다면 그쪽)로 바꿔야 한다. 레시피 카탈로그의 Boot 4.0 스텝에 이 패턴을 사전 탐지/치환하는 하위 레시피를 추가하는 걸 검토할 가치가 있다.
2. **`JavaTimeModule`을 직접 생성/등록하는 코드는 전부 깨진다** (Jackson 3에서 불필요해짐 + 클래스 자체가 없음). 마찬가지로 사전 탐지 대상.
3. **컴파일 에러가 여러 무관한 클래스에서 `@Slf4j`의 `log`나 Lombok 생성 getter를 못 찾는 형태로 무더기로 뜨면, 그 파일들을 고치려 하지 말고 가장 이른(재시도 0회차) 컴파일 로그의 에러부터 확인한다.** 십중팔구 그 배치 안 다른 곳의 진짜 하드 에러가 원인이다. (이건 Jackson/Boot 4에 국한되지 않는, javac+어노테이션 프로세서의 일반적인 특성.)
4. **AI가 "고친" 코드가 오히려 원래 맞았던 코드를 틀리게 만들 수 있다** — 재시도 중간 결과를 무조건 신뢰하지 말고, 최종적으로 실제 `mvn compile`/`javap`로 클래스 구조를 직접 검증하는 게 확실하다.

## 참고 / 다음 이슈 (범위 밖)

`mvn test-compile`에서 별도로 발견(이번 사례와 무관, 고치지 않음):

```
[ERROR] EmailUtilTest.java:[13,51] package org.springframework.boot.autoconfigure.mail does not exist
[ERROR] EmailUtilTest.java:[37,31] cannot find symbol
  symbol:   class MailSenderAutoConfiguration
```

Boot 4에서 mail 오토컨피그 패키지가 옮겨졌거나 이름이 바뀐 것으로 보인다 — Boot 4 마이그레이션을 계속하면 다음에 만날 별개의 갭. 별도 레슨으로 다룰 것.

## 관련 파일

- 고친 파일: `backend/data/jobs/44/work/ace-common/src/main/java/com/poscodx/ai/ace/util/JacksonUtil.java` (job 데이터, 이 도구의 소스 저장소 밖 — 별도 per-job git 저장소)
- 원본 인수인계 가이드: `backend/data/jobs/44/output/handoff/stage1-guide.md`
- 관련 카탈로그 항목: `backend/app/mvnrewrite/recipe_catalog.yaml`의 `spring_boot_steps` 중 `from: "3.5", to: "4.0"`
