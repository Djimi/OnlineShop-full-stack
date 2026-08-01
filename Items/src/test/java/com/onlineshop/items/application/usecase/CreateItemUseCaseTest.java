package com.onlineshop.items.application.usecase;

import com.onlineshop.items.application.command.CreateItemCommand;
import com.onlineshop.items.application.dto.CreateItemResponse;
import com.onlineshop.items.application.dto.mapper.ItemResponseMapper;
import com.onlineshop.items.domain.aggregateroots.Item;
import com.onlineshop.items.domain.event.ItemCreated;
import com.onlineshop.items.domain.repository.ItemRepository;
import com.onlineshop.items.domain.service.IdGenerator;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Captor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class CreateItemUseCaseTest {

    private static final String ITEM_NAME = "New Item";
    private static final int ITEM_QUANTITY = 5;
    private static final String ITEM_DESCRIPTION = "A new item";

    @Mock
    private ItemRepository itemRepository;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    @Mock
    private IdGenerator idGenerator;

    private final ItemResponseMapper mapper = new ItemResponseMapper();

    private CreateItemUseCase createItemUseCase;

    @Captor
    private ArgumentCaptor<ItemCreated> itemCreatedCaptor;

    @BeforeEach
    void setUp() {
        createItemUseCase = new CreateItemUseCase(itemRepository, eventPublisher, idGenerator, mapper);
    }

    @Test
    void execute_whenValidCommand_createsItemAndPublishesEvent() {
        UUID generatedId = UUID.randomUUID();
        when(idGenerator.generate()).thenReturn(generatedId);
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        CreateItemCommand command = new CreateItemCommand(ITEM_NAME, ITEM_QUANTITY, ITEM_DESCRIPTION);
        CreateItemResponse response = createItemUseCase.execute(command);

        assertThat(response.id()).isEqualTo(generatedId);
        assertThat(response.name()).isEqualTo(ITEM_NAME);
        assertThat(response.quantity()).isEqualTo(ITEM_QUANTITY);
        assertThat(response.description()).isEqualTo(ITEM_DESCRIPTION);

        verify(itemRepository).save(any(Item.class));
        verify(eventPublisher).publishEvent(itemCreatedCaptor.capture());

        ItemCreated event = itemCreatedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(generatedId);
        assertThat(event.getName().value()).isEqualTo(ITEM_NAME);
        assertThat(event.getQuantity().amount()).isEqualTo(ITEM_QUANTITY);
        assertThat(event.getDescription().value()).isEqualTo(ITEM_DESCRIPTION);
    }

    @Test
    void execute_whenValidCommandWithNullDescription_returnsEmptyDescription() {
        UUID generatedId = UUID.randomUUID();
        when(idGenerator.generate()).thenReturn(generatedId);
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        CreateItemCommand command = new CreateItemCommand("No Desc Item", 3, null);
        CreateItemResponse response = createItemUseCase.execute(command);

        assertThat(response.id()).isEqualTo(generatedId);
        assertThat(response.name()).isEqualTo("No Desc Item");
        assertThat(response.quantity()).isEqualTo(3);
        assertThat(response.description()).isEmpty();

        verify(eventPublisher).publishEvent(itemCreatedCaptor.capture());

        ItemCreated event = itemCreatedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(generatedId);
        assertThat(event.getName().value()).isEqualTo("No Desc Item");
        assertThat(event.getQuantity().amount()).isEqualTo(3);
        assertThat(event.getDescription()).isNotNull();
        assertThat(event.getDescription().value()).isEmpty();
    }

    @Test
    void execute_whenValidCommand_zeroQuantity() {
        UUID generatedId = UUID.randomUUID();
        when(idGenerator.generate()).thenReturn(generatedId);
        when(itemRepository.save(any(Item.class))).thenAnswer(invocation -> invocation.getArgument(0));

        CreateItemCommand command = new CreateItemCommand("Zero Qty Item", 0, "Item with zero quantity");
        CreateItemResponse response = createItemUseCase.execute(command);

        assertThat(response.quantity()).isZero();

        verify(eventPublisher).publishEvent(itemCreatedCaptor.capture());

        ItemCreated event = itemCreatedCaptor.getValue();
        assertThat(event.getItemId().getValue()).isEqualTo(generatedId);
        assertThat(event.getName().value()).isEqualTo("Zero Qty Item");
        assertThat(event.getQuantity().amount()).isZero();
        assertThat(event.getDescription().value()).isEqualTo("Item with zero quantity");
    }
}
