package com.onlineshop.items.application.usecase;

import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.application.command.UpdateItemCommand;
import com.onlineshop.items.application.dto.UpdateItemResponse;
import com.onlineshop.items.application.dto.mapper.ItemResponseMapper;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.event.ItemUpdated;
import com.onlineshop.items.domain.exception.ItemNotFoundException;
import com.onlineshop.items.domain.repository.ItemRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Captor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UpdateItemUseCaseTest {

    @Mock
    private ItemRepository itemRepository;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    private final ItemResponseMapper mapper = new ItemResponseMapper();

    private UpdateItemUseCase updateItemUseCase;

    @Captor
    private ArgumentCaptor<ItemUpdated> itemUpdatedCaptor;

    @BeforeEach
    void setUp() {
        updateItemUseCase = new UpdateItemUseCase(itemRepository, eventPublisher, mapper);
    }

    @Test
    void execute_whenItemFoundAndChanged_updatesAndPublishesEvent() {
        UUID itemId = UUID.randomUUID();
        Item existingItem = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("Old Name"),
            new Quantity(5),
            new ItemDescription("Old description")
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(existingItem));
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        UpdateItemCommand command = new UpdateItemCommand(itemId, "New Name", 10, "New description");
        UpdateItemResponse response = updateItemUseCase.execute(command);

        assertThat(response.id()).isEqualTo(itemId);
        assertThat(response.name()).isEqualTo("New Name");
        assertThat(response.quantity()).isEqualTo(10);
        assertThat(response.description()).isEqualTo("New description");

        verify(eventPublisher).publishEvent(itemUpdatedCaptor.capture());

        ItemUpdated event = itemUpdatedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(itemId);
        assertThat(event.getName().value()).isEqualTo("New Name");
        assertThat(event.getQuantity().amount()).isEqualTo(10);
        assertThat(event.getDescription().value()).isEqualTo("New description");
    }

    @Test
    void execute_whenItemNotFound_throwsItemNotFoundException() {
        UUID itemId = UUID.randomUUID();
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.empty());

        assertThatThrownBy(() ->
            updateItemUseCase.execute(new UpdateItemCommand(itemId, "Name", 5, "Desc"))
        ).isInstanceOf(ItemNotFoundException.class)
            .hasMessageContaining(itemId.toString());
    }

    @Test
    void execute_whenNoChanges_stillSavesAndNoEventPublished() {
        UUID itemId = UUID.randomUUID();
        Item existingItem = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("Same Name"),
            new Quantity(10),
            new ItemDescription("Same description")
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(existingItem));
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        UpdateItemCommand command = new UpdateItemCommand(itemId, "Same Name", 10, "Same description");
        UpdateItemResponse response = updateItemUseCase.execute(command);

        assertThat(response.name()).isEqualTo("Same Name");
        verify(itemRepository).save(any(Item.class));
        verify(eventPublisher, never()).publishEvent(any());
    }

    @Test
    void execute_whenItemFoundWithNullDescription_updatesCorrectly() {
        UUID itemId = UUID.randomUUID();
        Item existingItem = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("Item"),
            new Quantity(5),
            null
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(existingItem));
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        UpdateItemCommand command = new UpdateItemCommand(itemId, "Item", 5, "Now has description");
        UpdateItemResponse response = updateItemUseCase.execute(command);

        assertThat(response.description()).isEqualTo("Now has description");

        verify(eventPublisher).publishEvent(itemUpdatedCaptor.capture());

        ItemUpdated event = itemUpdatedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(itemId);
        assertThat(event.getDescription().value()).isEqualTo("Now has description");
    }
}
