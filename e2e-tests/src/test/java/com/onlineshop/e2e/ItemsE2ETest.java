package com.onlineshop.e2e;

import io.restassured.response.Response;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;
import static org.junit.jupiter.api.Assertions.*;

class ItemsE2ETest extends BaseTest {

    @Test
    @DisplayName("Test 1: Register user, login, get all items, get single item")
    void testAuthenticatedItemsFlow() {
        // Generate random username to avoid conflicts
        String randomUsername = "testuser_" + UUID.randomUUID().toString().substring(0, 8);
        String password = "testPassword123";

        // Step 1: Create/Register user
        Map<String, String> registerRequest = Map.of(
                "username", randomUsername,
                "password", password
        );

        Response registerResponse = given()
                .spec(requestSpec)
                .body(registerRequest)
                .when()
                .post("/auth/register")
                .then()
                .statusCode(201)
                .body("username", equalTo(randomUsername))
                .body("userId", notNullValue())
                .extract()
                .response();

        Long userId = registerResponse.jsonPath().getLong("userId");
        assertNotNull(userId, "User ID should not be null after registration");

        // Step 2: Login with the user and store the token
        Map<String, String> loginRequest = Map.of(
                "username", randomUsername,
                "password", password
        );

        Response loginResponse = given()
                .spec(requestSpec)
                .body(loginRequest)
                .when()
                .post("/auth/login")
                .then()
                .statusCode(200)
                .body("token", notNullValue())
                .body("username", equalTo(randomUsername))
                .body("tokenType", equalTo("Bearer"))
                .extract()
                .response();

        String token = loginResponse.jsonPath().getString("token");
        assertNotNull(token, "Token should not be null after login");
        assertFalse(token.isEmpty(), "Token should not be empty");

        // Step 3: Get all items using the token
        Response allItemsResponse = given()
                .spec(requestSpec)
                .header("Authorization", "Bearer " +token)
                .when()
                .get("/items")
                .then()
                .statusCode(200)
                .body("$", instanceOf(List.class))
                .extract()
                .response();

        List<Map<String, Object>> items = allItemsResponse.jsonPath().getList("$");
        assertNotNull(items, "Items list should not be null");
        assertFalse(items.isEmpty(), "Items list should not be empty - staging seeds items deterministically");

        // Step 4: Get the first item from the list
        String firstItemId = (String) items.get(0).get("id");
        assertNotNull(firstItemId, "First item ID should not be null");

        given()
                .spec(requestSpec)
                .header("Authorization", "Bearer " +token)
                .when()
                .get("/items/" + firstItemId)
                .then()
                .statusCode(200)
                .body("id", equalTo(firstItemId))
                .body("name", notNullValue())
                .body("quantity", notNullValue());
    }

    @Test
    @DisplayName("Test 2: Get item without supplying token - should be unauthorized")
    void testGetItemWithoutToken() {
        given()
                .spec(requestSpec)
                .when()
                .get("/items")
                .then()
                .statusCode(401);
    }

    @Test
    @DisplayName("Test 3: Get item with non-existing/invalid token - should be unauthorized")
    void testGetItemWithInvalidToken() {
        String fakeToken = "non-existing-invalid-token-12345";

        given()
                .spec(requestSpec)
                .header("Authorization", "Bearer " +fakeToken)
                .when()
                .get("/items")
                .then()
                .statusCode(401);
    }
}
