package com.onlineshop.e2e;

import io.restassured.RestAssured;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.filter.log.LogDetail;
import io.restassured.http.ContentType;
import io.restassured.specification.RequestSpecification;
import org.junit.jupiter.api.BeforeAll;

public abstract class BaseTest {

    protected static final String BASE_URL = System.getenv().getOrDefault("E2E_BASE_URL", "http://localhost:10000");

    protected static RequestSpecification requestSpec;

    @BeforeAll
    static void setupRestAssured() {
        RestAssured.baseURI = BASE_URL;
        RestAssured.enableLoggingOfRequestAndResponseIfValidationFails(LogDetail.ALL);

        requestSpec = new RequestSpecBuilder()
                .setContentType(ContentType.JSON)
                .setAccept(ContentType.JSON)
                .build();
    }
}
