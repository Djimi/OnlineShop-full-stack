package com.onlineshop.items.application.usecase;

import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.application.command.DeleteItemCommand;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.event.ItemDeleted;
import com.onlineshop.items.domain.repository.ItemRepository;
import com.onlineshop.items.domain.exception.ItemNotFoundException;
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
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeleteItemUseCaseTest {

    @Mock
    private ItemRepository itemRepository;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    private DeleteItemUseCase deleteItemUseCase;

    @Captor
    private ArgumentCaptor<ItemDeleted> itemDeletedCaptor;

    @BeforeEach
    void setUp() {
        deleteItemUseCase = new DeleteItemUseCase(itemRepository, eventPublisher);
    }

    @Test
    void execute_whenItemFound_deletesAndPublishesEvent() {
        UUID itemId = UUID.randomUUID();
        Item item = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("Item to Delete"),
            new Quantity(5),
            new ItemDescription("Will be deleted")
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(item));

        deleteItemUseCase.execute(new DeleteItemCommand(itemId));

        verify(itemRepository).delete(item);
        verify(eventPublisher).publishEvent(itemDeletedCaptor.capture());

        ItemDeleted event = itemDeletedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(itemId);
    }

    @Test
    void execute_whenItemNotFound_throwsItemNotFoundException() {
        UUID itemId = UUID.randomUUID();
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.empty());

        assertThatThrownBy(() -> deleteItemUseCase.execute(new DeleteItemCommand(itemId)))
            .isInstanceOf(ItemNotFoundException.class)
            .hasMessageContaining(itemId.toString());
    }
}
