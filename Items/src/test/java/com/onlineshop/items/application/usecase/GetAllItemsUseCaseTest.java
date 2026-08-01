package com.onlineshop.items.application.usecase;

import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
import com.onlineshop.items.application.dto.GetItemResponse;
import com.onlineshop.items.application.dto.mapper.ItemResponseMapper;
import com.onlineshop.items.application.query.GetAllItemsQuery;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.repository.ItemRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GetAllItemsUseCaseTest {

    @Mock
    private ItemRepository itemRepository;

    private final ItemResponseMapper mapper = new ItemResponseMapper();

    private GetAllItemsUseCase getAllItemsUseCase;

    @BeforeEach
    void setUp() {
        getAllItemsUseCase = new GetAllItemsUseCase(itemRepository, mapper);
    }

    @Test
    void execute_whenItemsExist_returnsAllItems() {
        Item item1 = Item.fromPersistence(
            new ItemId(UUID.randomUUID()),
            new ItemName("Item 1"),
            new Quantity(5),
            new ItemDescription("First item")
        );
        Item item2 = Item.fromPersistence(
            new ItemId(UUID.randomUUID()),
            new ItemName("Item 2"),
            new Quantity(10),
            new ItemDescription("Second item")
        );
        when(itemRepository.findAll()).thenReturn(List.of(item1, item2));

        List<GetItemResponse> responses = getAllItemsUseCase.execute(new GetAllItemsQuery());

        assertThat(responses).hasSize(2);
        assertThat(responses).extracting(GetItemResponse::name)
            .containsExactly("Item 1", "Item 2");
    }

    @Test
    void execute_whenNoItems_returnsEmptyList() {
        when(itemRepository.findAll()).thenReturn(List.of());

        List<GetItemResponse> responses = getAllItemsUseCase.execute(new GetAllItemsQuery());

        assertThat(responses).isEmpty();
    }

    @Test
    void execute_whenItemsWithNullDescriptions_mapsCorrectly() {
        Item item = Item.fromPersistence(
            new ItemId(UUID.randomUUID()),
            new ItemName("No Desc"),
            new Quantity(3),
            null
        );
        when(itemRepository.findAll()).thenReturn(List.of(item));

        List<GetItemResponse> responses = getAllItemsUseCase.execute(new GetAllItemsQuery());

        assertThat(responses).hasSize(1);
        assertThat(responses.get(0).description()).isEmpty();
    }
}
