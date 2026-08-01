package com.onlineshop.items.application.usecase;

import com.onlineshop.items.application.dto.GetItemResponse;
import com.onlineshop.items.application.dto.mapper.ItemResponseMapper;
import com.onlineshop.items.application.query.SearchItemsByDescriptionQuery;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.repository.ItemRepository;
import com.onlineshop.items.domain.valueobject.ItemDescription;
import com.onlineshop.items.domain.valueobject.ItemId;
import com.onlineshop.items.domain.valueobject.ItemName;
import com.onlineshop.items.domain.valueobject.Quantity;
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
class SearchItemsUseCaseTest {

    @Mock
    private ItemRepository itemRepository;

    private final ItemResponseMapper mapper = new ItemResponseMapper();

    private SearchItemsUseCase searchItemsUseCase;

    @BeforeEach
    void setUp() {
        searchItemsUseCase = new SearchItemsUseCase(itemRepository, mapper);
    }

    @Test
    void execute_whenItemsFoundWithSearchTerm_returnsMatchingItems() {
        String searchTerm = "laptop";
        UUID item1Id = UUID.randomUUID();
        UUID item2Id = UUID.randomUUID();

        Item item1 = Item.fromPersistence(
                new ItemId(item1Id),
                new ItemName("Gaming Laptop"),
                new Quantity(5),
                new ItemDescription("High-performance gaming laptop with RTX 4090")
        );

        Item item2 = Item.fromPersistence(
                new ItemId(item2Id),
                new ItemName("Business Laptop"),
                new Quantity(10),
                new ItemDescription("Professional laptop for business use")
        );

        when(itemRepository.searchByDescription(searchTerm)).thenReturn(List.of(item1, item2));

        SearchItemsByDescriptionQuery query = new SearchItemsByDescriptionQuery(searchTerm);
        List<GetItemResponse> responses = searchItemsUseCase.execute(query);

        assertThat(responses).hasSize(2);

        GetItemResponse response1 = responses.get(0);
        assertThat(response1.id()).isEqualTo(item1Id);
        assertThat(response1.name()).isEqualTo("Gaming Laptop");
        assertThat(response1.quantity()).isEqualTo(5);
        assertThat(response1.description()).isEqualTo("High-performance gaming laptop with RTX 4090");

        GetItemResponse response2 = responses.get(1);
        assertThat(response2.id()).isEqualTo(item2Id);
        assertThat(response2.name()).isEqualTo("Business Laptop");
        assertThat(response2.quantity()).isEqualTo(10);
        assertThat(response2.description()).isEqualTo("Professional laptop for business use");
    }

    @Test
    void execute_whenNoItemsFound_returnsEmptyList() {
        String searchTerm = "nonexistent";
        when(itemRepository.searchByDescription(searchTerm)).thenReturn(List.of());

        SearchItemsByDescriptionQuery query = new SearchItemsByDescriptionQuery(searchTerm);
        List<GetItemResponse> responses = searchItemsUseCase.execute(query);

        assertThat(responses).isEmpty();
    }

    @Test
    void execute_whenItemHasEmptyDescription_handlesGracefully() {
        String searchTerm = "mouse";
        UUID itemId = UUID.randomUUID();

        Item item = Item.fromPersistence(
                new ItemId(itemId),
                new ItemName("Wireless Mouse"),
                new Quantity(20),
                new ItemDescription("")
        );

        when(itemRepository.searchByDescription(searchTerm)).thenReturn(List.of(item));

        SearchItemsByDescriptionQuery query = new SearchItemsByDescriptionQuery(searchTerm);
        List<GetItemResponse> responses = searchItemsUseCase.execute(query);

        assertThat(responses).hasSize(1);
        GetItemResponse response = responses.get(0);
        assertThat(response.id()).isEqualTo(itemId);
        assertThat(response.name()).isEqualTo("Wireless Mouse");
        assertThat(response.quantity()).isEqualTo(20);
        assertThat(response.description()).isEmpty();
    }

    @Test
    void execute_whenMultipleItemsMatch_returnsAllMatches() {
        String searchTerm = "wireless";
        UUID item1Id = UUID.randomUUID();
        UUID item2Id = UUID.randomUUID();
        UUID item3Id = UUID.randomUUID();

        Item item1 = Item.fromPersistence(
                new ItemId(item1Id),
                new ItemName("Mouse"),
                new Quantity(15),
                new ItemDescription("Wireless mouse with Bluetooth")
        );

        Item item2 = Item.fromPersistence(
                new ItemId(item2Id),
                new ItemName("Keyboard"),
                new Quantity(8),
                new ItemDescription("Mechanical wireless keyboard")
        );

        Item item3 = Item.fromPersistence(
                new ItemId(item3Id),
                new ItemName("Headset"),
                new Quantity(12),
                new ItemDescription("Wireless gaming headset with noise cancellation")
        );

        when(itemRepository.searchByDescription(searchTerm)).thenReturn(List.of(item1, item2, item3));

        SearchItemsByDescriptionQuery query = new SearchItemsByDescriptionQuery(searchTerm);
        List<GetItemResponse> responses = searchItemsUseCase.execute(query);

        assertThat(responses).hasSize(3);
        assertThat(responses).extracting(GetItemResponse::name)
                .containsExactly("Mouse", "Keyboard", "Headset");
    }
}
