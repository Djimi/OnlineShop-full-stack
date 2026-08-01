# syntax=docker/dockerfile:1

FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /build

COPY common/ common/
COPY Items/ Items/
RUN --mount=type=cache,target=/root/.m2,id=maven-repo \
    mvn -f common/pom.xml install -DskipTests -q && \
    mvn -f Items/pom.xml package -DskipTests -q

FROM eclipse-temurin:25.0.1_8-jre-alpine-3.23
RUN apk --no-cache add curl
WORKDIR /app
COPY --from=build /build/Items/target/*.jar app.jar
EXPOSE 9000
ENTRYPOINT ["java", "-jar", "app.jar"]
