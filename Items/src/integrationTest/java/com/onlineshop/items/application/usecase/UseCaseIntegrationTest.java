package com.onlineshop.items.application.usecase;

import com.onlineshop.items.application.command.CreateItemCommand;
import com.onlineshop.items.application.command.DeleteItemCommand;
import com.onlineshop.items.application.command.UpdateItemCommand;
import com.onlineshop.items.application.dto.CreateItemResponse;
import com.onlineshop.items.application.dto.UpdateItemResponse;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.exception.ItemNotFoundException;
import com.onlineshop.items.domain.repository.ItemRepository;
import com.onlineshop.items.domain.valueobject.ItemId;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest(webEnvironment = WebEnvironment.NONE, properties = {
        "spring.jpa.hibernate.ddl-auto=create-drop"
})
@Testcontainers
class UseCaseIntegrationTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:18-alpine");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private CreateItemUseCase createItemUseCase;

    @Autowired
    private UpdateItemUseCase updateItemUseCase;

    @Autowired
    private DeleteItemUseCase deleteItemUseCase;

    @Autowired
    private ItemRepository itemRepository;

    @Test
    void createItem_shouldPersistAndReturnResponse() {
        CreateItemCommand cmd = new CreateItemCommand("Test Item", 10, "A test item");

        CreateItemResponse response = createItemUseCase.execute(cmd);

        assertThat(response.name()).isEqualTo("Test Item");
        assertThat(response.quantity()).isEqualTo(10);
        assertThat(response.description()).isEqualTo("A test item");
        assertThat(response.id()).isNotNull();

        Optional<Item> found = itemRepository.findById(new ItemId(response.id()));
        assertThat(found).isPresent();
        assertThat(found.get().getName().value()).isEqualTo("Test Item");
    }

    @Test
    void createItem_withNullDescription_shouldPersistWithEmpty() {
        CreateItemCommand cmd = new CreateItemCommand("No Desc Item", 5, null);

        CreateItemResponse response = createItemUseCase.execute(cmd);

        assertThat(response.description()).isEmpty();
    }

    @Test
    void updateItem_shouldUpdateAndReturnResponse() {
        CreateItemCommand createCmd = new CreateItemCommand("Original", 10, "Original description");
        CreateItemResponse created = createItemUseCase.execute(createCmd);

        UpdateItemCommand updateCmd = new UpdateItemCommand(created.id(), "Updated", 20, "Updated description");
        UpdateItemResponse response = updateItemUseCase.execute(updateCmd);

        assertThat(response.name()).isEqualTo("Updated");
        assertThat(response.quantity()).isEqualTo(20);
        assertThat(response.description()).isEqualTo("Updated description");

        Optional<Item> found = itemRepository.findById(new ItemId(created.id()));
        assertThat(found).isPresent();
        assertThat(found.get().getName().value()).isEqualTo("Updated");
        assertThat(found.get().getQuantity().amount()).isEqualTo(20);
    }

    @Test
    void updateItem_shouldThrowWhenItemNotFound() {
        UpdateItemCommand cmd = new UpdateItemCommand(UUID.randomUUID(), "Ghost", 1, "Does not exist");

        assertThatThrownBy(() -> updateItemUseCase.execute(cmd))
                .isInstanceOf(ItemNotFoundException.class);
    }

    @Test
    void deleteItem_shouldRemoveItem() {
        CreateItemCommand createCmd = new CreateItemCommand("To Delete", 3, "Will be deleted");
        CreateItemResponse created = createItemUseCase.execute(createCmd);

        deleteItemUseCase.execute(new DeleteItemCommand(created.id()));

        Optional<Item> found = itemRepository.findById(new ItemId(created.id()));
        assertThat(found).isEmpty();
    }

    @Test
    void deleteItem_shouldThrowWhenItemNotFound() {
        assertThatThrownBy(() -> deleteItemUseCase.execute(new DeleteItemCommand(UUID.randomUUID())))
                .isInstanceOf(ItemNotFoundException.class);
    }
}
