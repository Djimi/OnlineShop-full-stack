package com.onlineshop.items.domain.aggregateroots;

import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.domain.event.ItemCreated;
import com.onlineshop.items.domain.event.ItemDeleted;
import com.onlineshop.items.domain.event.ItemUpdated;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ItemTest {

    @Test
    void createNew_whenValidData_createsItemWithCreatedEvent() {
        ItemId id = new ItemId(UUID.randomUUID());
        ItemName name = new ItemName("Test Item");
        Quantity quantity = new Quantity(10);
        ItemDescription description = new ItemDescription("A test item");

        Item item = Item.createNew(id, name, quantity, description);

        assertThat(item.getId()).isEqualTo(id);
        assertThat(item.getName()).isEqualTo(name);
        assertThat(item.getQuantity()).isEqualTo(quantity);
        assertThat(item.getDescription()).isEqualTo(description);
        assertThat(item.getDomainEvents()).hasSize(1)
            .first()
            .isInstanceOfSatisfying(ItemCreated.class, event -> {
                assertThat(event.getItemId()).isEqualTo(id);
                assertThat(event.getName()).isEqualTo(name);
                assertThat(event.getQuantity()).isEqualTo(quantity);
                assertThat(event.getDescription()).isEqualTo(description);
            });
    }

    @Test
    void createNew_whenNullId_throwsException() {
        ItemName name = new ItemName("Test Item");
        Quantity quantity = new Quantity(10);

        assertThatThrownBy(() -> Item.createNew(null, name, quantity, null))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Item ID is required");
    }

    @Test
    void fromPersistence_whenValidData_reconstitutesItemWithoutEvents() {
        ItemId id = new ItemId(UUID.randomUUID());
        ItemName name = new ItemName("Test Item");
        Quantity quantity = new Quantity(10);
        ItemDescription description = new ItemDescription("A test item");

        Item item = Item.fromPersistence(id, name, quantity, description);

        assertThat(item.getId()).isEqualTo(id);
        assertThat(item.getName()).isEqualTo(name);
        assertThat(item.getQuantity()).isEqualTo(quantity);
        assertThat(item.getDescription()).isEqualTo(description);
        assertThat(item.getDomainEvents()).isEmpty();
    }

    @Test
    void fromPersistence_whenNullDescription_reconstitutesItem() {
        ItemId id = new ItemId(UUID.randomUUID());
        ItemName name = new ItemName("Test Item");
        Quantity quantity = new Quantity(10);

        Item item = Item.fromPersistence(id, name, quantity, null);

        assertThat(item.getDescription().value()).isEmpty();
        assertThat(item.getDomainEvents()).isEmpty();
    }

    @Test
    void updateDetails_whenValuesChanged_updatesAndRegistersUpdatedEvent() {
        Item item = createDefaultItem();
        ItemName newName = new ItemName("Updated Name");
        Quantity newQuantity = new Quantity(20);
        ItemDescription newDescription = new ItemDescription("Updated description");
        item.clearDomainEvents();

        item.updateDetails(newName, newQuantity, newDescription);

        assertThat(item.getName()).isEqualTo(newName);
        assertThat(item.getQuantity()).isEqualTo(newQuantity);
        assertThat(item.getDescription()).isEqualTo(newDescription);
        assertThat(item.getDomainEvents()).hasSize(1)
            .first()
            .isInstanceOfSatisfying(ItemUpdated.class, event -> {
                assertThat(event.getItemId()).isEqualTo(item.getId());
                assertThat(event.getName()).isEqualTo(newName);
                assertThat(event.getQuantity()).isEqualTo(newQuantity);
                assertThat(event.getDescription()).isEqualTo(newDescription);
            });
    }

    @Test
    void updateDetails_whenNoChanges_doesNotRegisterEvent() {
        ItemName name = new ItemName("Same Name");
        Quantity quantity = new Quantity(10);
        ItemDescription description = new ItemDescription("Same description");
        Item item = Item.createNew(new ItemId(UUID.randomUUID()), name, quantity, description);
        item.clearDomainEvents();

        item.updateDetails(name, quantity, description);

        assertThat(item.getDomainEvents()).isEmpty();
    }

    @Test
    void updateDetails_whenNullName_throwsException() {
        Item item = createDefaultItem();

        assertThatThrownBy(() -> item.updateDetails(null, new Quantity(10), null))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Item name is required");
    }

    @Test
    void updateDetails_whenNullQuantity_throwsException() {
        Item item = createDefaultItem();

        assertThatThrownBy(() -> item.updateDetails(new ItemName("Name"), null, null))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Quantity is required");
    }

    @Test
    void markAsDeleted_registersDeletedEvent() {
        Item item = createDefaultItem();
        item.clearDomainEvents();

        item.markAsDeleted();

        assertThat(item.getDomainEvents()).hasSize(1)
            .first()
            .isInstanceOfSatisfying(ItemDeleted.class, event ->
                assertThat(event.getItemId()).isEqualTo(item.getId())
            );
    }

    private static Item createDefaultItem() {
        return Item.createNew(
            new ItemId(UUID.randomUUID()),
            new ItemName("Default"),
            new Quantity(5),
            new ItemDescription("Default description")
        );
    }
}
