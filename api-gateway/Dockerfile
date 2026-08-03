# syntax=docker/dockerfile:1

FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /build

COPY pom.xml .
COPY src/ src/
RUN --mount=type=cache,target=/root/.m2,id=maven-repo \
    mvn package -DskipTests -q

FROM eclipse-temurin:25.0.1_8-jre-alpine-3.23

RUN apk --no-cache add curl

WORKDIR /app

COPY --from=build /build/target/*.jar app.jar

EXPOSE 10000

ENTRYPOINT ["java", "-jar", "app.jar"]
