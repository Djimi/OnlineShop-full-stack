package com.onlineshop.items.application.usecase;

import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.application.dto.GetItemResponse;
import com.onlineshop.items.application.dto.mapper.ItemResponseMapper;
import com.onlineshop.items.application.query.GetItemQuery;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.exception.ItemNotFoundException;
import com.onlineshop.items.domain.repository.ItemRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GetItemUseCaseTest {

    @Mock
    private ItemRepository itemRepository;

    private final ItemResponseMapper mapper = new ItemResponseMapper();

    private GetItemUseCase getItemUseCase;

    @BeforeEach
    void setUp() {
        getItemUseCase = new GetItemUseCase(itemRepository, mapper);
    }

    @Test
    void execute_whenItemFound_returnsItemResponse() {
        UUID itemId = UUID.randomUUID();
        Item item = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("Test Item"),
            new Quantity(10),
            new ItemDescription("A test item")
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(item));

        GetItemResponse response = getItemUseCase.execute(new GetItemQuery(itemId));

        assertThat(response.id()).isEqualTo(itemId);
        assertThat(response.name()).isEqualTo("Test Item");
        assertThat(response.quantity()).isEqualTo(10);
        assertThat(response.description()).isEqualTo("A test item");
    }

    @Test
    void execute_whenItemNotFound_throwsItemNotFoundException() {
        UUID itemId = UUID.randomUUID();
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.empty());

        assertThatThrownBy(() -> getItemUseCase.execute(new GetItemQuery(itemId)))
            .isInstanceOf(ItemNotFoundException.class)
            .hasMessageContaining(itemId.toString());
    }

    @Test
    void execute_whenItemFoundWithNullDescription_returnsEmptyDescription() {
        UUID itemId = UUID.randomUUID();
        Item item = Item.fromPersistence(
            new ItemId(itemId),
            new ItemName("No Desc Item"),
            new Quantity(5),
            null
        );
        when(itemRepository.findById(new ItemId(itemId))).thenReturn(Optional.of(item));

        GetItemResponse response = getItemUseCase.execute(new GetItemQuery(itemId));

        assertThat(response.description()).isEmpty();
    }
}
