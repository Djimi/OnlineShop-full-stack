package com.onlineshop.e2e;

import com.sun.net.httpserver.HttpServer;
import io.restassured.RestAssured;
import io.restassured.config.RestAssuredConfig;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertAll;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RestAssuredLoggingTest extends BaseTest {

    @Test
    void validationFailure_withAuthenticatedRequest_omitsBodiesAndRedactsAuthorization() throws IOException {
        String password = "logging-regression-password-" + UUID.randomUUID();
        String bearerToken = "logging-regression-bearer-" + UUID.randomUUID();
        ByteArrayOutputStream diagnosticBytes = new ByteArrayOutputStream();
        PrintStream diagnosticStream = new PrintStream(diagnosticBytes, true, StandardCharsets.UTF_8);
        RestAssuredConfig originalConfig = RestAssured.config;
        HttpServer server = createServer(password);

        RestAssured.config = RestAssured.config.logConfig(
                RestAssured.config.getLogConfig().defaultStream(diagnosticStream)
        );
        server.start();

        try {
            AssertionError failure = assertThrows(AssertionError.class, () -> given()
                    .spec(requestSpec)
                    .config(RestAssured.config)
                    .header("aUtHoRiZaTiOn", "Bearer " + bearerToken)
                    .body(Map.of("password", password))
                    .when()
                    .post("http://" + InetAddress.getLoopbackAddress().getHostAddress() + ":" + server.getAddress().getPort())
                    .then()
                    .statusCode(201));

            String diagnostics = diagnosticBytes.toString(StandardCharsets.UTF_8);
            assertAll(
                    () -> assertFalse(diagnostics.contains(password), "Failure diagnostics must omit request and response bodies"),
                    () -> assertFalse(diagnostics.contains(bearerToken), "Failure diagnostics must redact Authorization values"),
                    () -> assertTrue(diagnostics.contains("[ BLACKLISTED ]"), "Failure diagnostics must show the Authorization blacklist marker"),
                    () -> assertTrue(failure.getMessage().contains("200"), "Failure diagnostics must retain the response status"),
                    () -> assertTrue(diagnostics.toLowerCase(Locale.ROOT).contains("x-diagnostic-context"), "Failure diagnostics must retain response headers")
            );
        } finally {
            server.stop(0);
            diagnosticStream.close();
            RestAssured.config = originalConfig;
        }
    }

    private HttpServer createServer(String password) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", exchange -> {
            exchange.getRequestBody().transferTo(OutputStream.nullOutputStream());
            byte[] response = ("{\"password\":\"" + password + "\"}").getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.getResponseHeaders().set("X-Diagnostic-Context", "available");
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream responseBody = exchange.getResponseBody()) {
                responseBody.write(response);
            }
        });
        return server;
    }
}
