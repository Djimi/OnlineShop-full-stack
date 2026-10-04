# syntax=docker/dockerfile:1
FROM eclipse-temurin:25.0.1_8-jdk-alpine-3.23

RUN apk --no-cache add curl=8.22.0-r0 \
    && addgroup -g 10001 tests \
    && adduser -D -u 10001 -G tests tests
WORKDIR /workspace/e2e-tests
COPY .mvn/ .mvn/
COPY mvnw pom.xml ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.m2,id=maven-repo \
    ./mvnw --batch-mode dependency:go-offline test-compile -DskipTests \
    && mkdir -p /home/tests/.m2 \
    && cp -a /root/.m2/repository /home/tests/.m2/repository \
    && chown -R tests:tests /workspace /home/tests
USER tests
ENV E2E_BASE_URL=http://api-gateway:10000
CMD ["./mvnw", "--batch-mode", "clean", "test"]
