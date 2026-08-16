package com.onlineshop.items.web.controller;

import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.repository.ItemRepository;
import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.web.dto.ErrorResponse;
import com.onlineshop.items.web.dto.ItemResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.RequestEntity;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.client.DefaultResponseErrorHandler;
import org.springframework.web.client.RestTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.net.URI;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "spring.jpa.hibernate.ddl-auto=create-drop"
})
@Testcontainers
class ItemsControllerE2eTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:18-alpine");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @LocalServerPort
    private int port;

    @Autowired
    private ItemRepository itemRepository;

    private RestTemplate restTemplate;
    private String baseUrl;

    @BeforeEach
    void setUp() {
        restTemplate = new RestTemplate();

        // RestTemplate throws on non-2xx by default. We suppress that so tests
        // can assert on status codes like 404 via ResponseEntity.getStatusCode().
        restTemplate.setErrorHandler(new DefaultResponseErrorHandler() {
            @Override
            public boolean hasError(ClientHttpResponse response) {
                return false;
            }
        });

        baseUrl = "http://localhost:" + port + "/api/v1/items";
    }

    @Test
    void fullLifecycle() {
        var createPayload = Map.of("name", "E2E Item", "quantity", 42, "description", "E2E desc");
        var createResponse = restTemplate.postForEntity(baseUrl, createPayload, ItemResponse.class);
        assertThat(createResponse.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(createResponse.getBody()).isNotNull();
        assertThat(createResponse.getBody().name()).isEqualTo("E2E Item");
        assertThat(createResponse.getBody().quantity()).isEqualTo(42);
        assertThat(createResponse.getBody().description()).isEqualTo("E2E desc");
        UUID id = createResponse.getBody().id();

        var createdItem = itemRepository.findById(new ItemId(id));
        assertThat(createdItem).isPresent();
        assertThat(createdItem.get().getName()).isEqualTo(new ItemName("E2E Item"));
        assertThat(createdItem.get().getQuantity()).isEqualTo(new Quantity(42));
        assertThat(createdItem.get().getDescription()).isEqualTo(new ItemDescription("E2E desc"));

        var getResponse = restTemplate.getForEntity(baseUrl + "/{id}", ItemResponse.class, id);
        assertThat(getResponse.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(getResponse.getBody().name()).isEqualTo("E2E Item");

        var updatePayload = Map.of("name", "Updated", "quantity", 10, "description", "Updated desc");
        restTemplate.put(baseUrl + "/{id}", updatePayload, id);

        var updatedItem = itemRepository.findById(new ItemId(id));
        assertThat(updatedItem).isPresent();
        assertThat(updatedItem.get().getName()).isEqualTo(new ItemName("Updated"));
        assertThat(updatedItem.get().getQuantity()).isEqualTo(new Quantity(10));

        var updatedResponse = restTemplate.getForEntity(baseUrl + "/{id}", ItemResponse.class, id);
        assertThat(updatedResponse.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(updatedResponse.getBody().name()).isEqualTo("Updated");
        assertThat(updatedResponse.getBody().quantity()).isEqualTo(10);

        restTemplate.delete(baseUrl + "/{id}", id);

        assertThat(itemRepository.findById(new ItemId(id))).isEmpty();

        var deletedResponse = restTemplate.getForEntity(baseUrl + "/{id}", ErrorResponse.class, id);
        assertThat(deletedResponse.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(deletedResponse.getBody().getTitle()).isEqualTo("Not Found");
        assertThat(deletedResponse.getBody().getDetail()).contains(id.toString());
    }

    @Test
    void createItem_withNullDescription() {
        var createPayload = Map.of("name", "Null Desc", "quantity", 1);
        var createResponse = restTemplate.postForEntity(baseUrl, createPayload, ItemResponse.class);
        assertThat(createResponse.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(createResponse.getBody().description()).isEmpty();

        var createdItem = itemRepository.findById(new ItemId(createResponse.getBody().id()));
        assertThat(createdItem).isPresent();
        assertThat(createdItem.get().getDescription().value()).isEmpty();
    }

    @Test
    void getAllItems_returnsList() {
        restTemplate.postForEntity(baseUrl, Map.of("name", "Item A", "quantity", 1), String.class);
        restTemplate.postForEntity(baseUrl, Map.of("name", "Item B", "quantity", 2), String.class);

        var allItems = itemRepository.findAll();
        assertThat(allItems).hasSizeGreaterThanOrEqualTo(2);

        var response = restTemplate.getForEntity(baseUrl, ItemResponse[].class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).hasSize(allItems.size());
    }

    @Test
    void searchItems_byDescription() {
        restTemplate.postForEntity(baseUrl, Map.of("name", "SearchMe", "quantity", 1, "description", "UniqueDesc"), String.class);

        var searchUrl = baseUrl + "/search?description=UniqueDesc";
        var response = restTemplate.getForEntity(searchUrl, ItemResponse[].class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).hasSize(1);
        assertThat(response.getBody()[0].name()).isEqualTo("SearchMe");

        var found = itemRepository.searchByDescription("UniqueDesc");
        assertThat(found).hasSize(1);
        assertThat(found.get(0).getName().value()).isEqualTo("SearchMe");
    }

    @Test
    void getItemById_notFound_returns404WithErrorBody() {
        var id = UUID.randomUUID();
        var response = restTemplate.getForEntity(baseUrl + "/{id}", ErrorResponse.class, id);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(response.getBody().getTitle()).isEqualTo("Not Found");
        assertThat(response.getBody().getStatus()).isEqualTo(404);
        assertThat(response.getBody().getDetail()).contains(id.toString());
    }

    @Test
    void deleteItem_notFound_returns404() {
        var id = UUID.randomUUID();
        var response = restTemplate.exchange(
                RequestEntity.delete(URI.create(baseUrl + "/" + id)).build(),
                ErrorResponse.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(response.getBody().getTitle()).isEqualTo("Not Found");
    }

    @Test
    void createItem_withNegativeQuantity_returns400() {
        var createPayload = Map.of("name", "Negative", "quantity", -5, "description", "desc");
        var createResponse = restTemplate.postForEntity(baseUrl, createPayload, ErrorResponse.class);
        assertThat(createResponse.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(createResponse.getBody().getTitle()).isEqualTo("Bad Request");
        assertThat(createResponse.getBody().getStatus()).isEqualTo(400);
        assertThat(createResponse.getBody().getDetail()).contains("Quantity cannot be negative");
    }

    @Test
    void searchItems_missingDescription_returns400() {
        var response = restTemplate.getForEntity(baseUrl + "/search", ErrorResponse.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody().getTitle()).isEqualTo("Bad Request");
        assertThat(response.getBody().getStatus()).isEqualTo(400);
        assertThat(response.getBody().getDetail()).contains("description");
    }

    @Test
    void searchItems_noMatch_returnsEmptyList() {
        var searchUrl = baseUrl + "/search?description=NonExistentXYZ";
        var response = restTemplate.getForEntity(searchUrl, ItemResponse[].class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isEmpty();
    }
}
