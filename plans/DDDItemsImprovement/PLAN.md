# DDD Items Improvement Plan

> Consolidated from 3 sub-agent investigations: Items service codebase audit, modern DDD best practices (2024-2025), and Spring Boot DDD implementation patterns.

---

## Current State Assessment

**Overall DDD Maturity Score: 5.5 / 10**

The service has solid DDD scaffolding — proper aggregate roots, value objects, domain events, repository ports, CQRS-lite command/query separation — but lacks domain depth. The event pipeline is dead code, the domain model is thin, and there are architectural violations.

| Category | Score | Key Gap |
|----------|-------|---------|
| Aggregate Design | 7/10 | Proper factory/reconstitution methods; slightly thin on business behavior |
| Value Objects | 7/10 | Clean records with validation; minor null-inconsistency, wrong package |
| Domain Events | 4/10 | Good event classes; **zero listeners** — entire pipeline is dead code |
| Repository Pattern | 9/10 | Clean domain interface + adapter; proper JPA separation with mapper |
| Domain Services | 5/10 | Only `IdGenerator` (technical, not business domain service) |
| Application Services | 7/10 | Clean use case pattern; web exception leaking, commands as web DTOs |
| ACL / Bounded Contexts | 5/10 | Item VOs in `common` library leak across bounded contexts |
| Testing | 2/10 | Single test file, no domain tests, no integration tests |

---

## Improvement Tasks

### Phase 1: Critical Fixes (Bug-level)

- [x] **1.1 — Fix `@PathVariable id` bug in `PUT /{id}`**
  - **File:** `ItemsController.java` lines 66-68
  - **Problem:** `@PathVariable UUID id` is received but never used. The `UpdateItemCommand` carries its own `UUID id` from the JSON body. A client could send `PUT /items/aaa-bbb` with body `{"id": "ccc-ddd", ...}` and item `ccc-ddd` would be updated instead of `aaa-bbb`.
  - **Fix:** Validate that `command.id().equals(id)` and return `400 Bad Request` via `ResponseStatusException` if they differ.

- [x] **1.2 — Add missing `@DeleteMapping` endpoint**
  - **File:** `ItemsController.java`
  - **Problem:** `DeleteItemUseCase` and `DeleteItemCommand` exist and are fully implemented, but there is no `@DeleteMapping` in the controller.
  - **Fix:** Added `@DeleteMapping("/{id}")` method constructing a `DeleteItemCommand(id)` from the path variable and calling `deleteItemUseCase.execute()`, returning `204 No Content`.

- [x] **1.3 — Move `ItemNotFoundException` out of `web.exception`**
  - **Files:** `web/exception/ItemNotFoundException.java`, `UpdateItemUseCase.java`, `DeleteItemUseCase.java`, `GetItemUseCase.java`, `GlobalExceptionHandler.java`
  - **Problem:** Application use cases import and throw `web.exception.ItemNotFoundException` — a reverse dependency (`application` → `web`). In DDD, the dependency arrow must point inward.
  - **Fix:** Created `domain/exception/ItemNotFoundException.java`. Removed the `web.exception` one. Updated `GlobalExceptionHandler` to map the new exception to HTTP 404. Also added `ResponseStatusException` handler for the new 400 validation.

### Phase 2: Architecture Cleanup

- [x] **2.1 — Implement domain event handler(s)**
  - **Fix:** Added `application/event/ItemDomainEventListener.java` with `@TransactionalEventListener(phase = AFTER_COMMIT)` methods for `ItemCreated`, `ItemUpdated`, and `ItemDeleted`. Validates the full event pipeline: `AggregateRoot.registerEvent()` → `ApplicationEventPublisher.publishEvent()` → `@TransactionalEventListener`.

- [x] **2.2 — Create separate web DTOs for requests**
  - **Files:** `ItemsController.java`, `web/dto/CreateItemRequest.java`, `web/dto/UpdateItemRequest.java`
  - **Problem:** `CreateItemCommand` and `UpdateItemCommand` were used directly as `@RequestBody` in the controller.
  - **Fix:** Created `web/dto/CreateItemRequest.java` and `web/dto/UpdateItemRequest.java`. Controller accepts these, maps to commands via private `toCommand()` methods. `UpdateItemRequest` no longer carries an `id` field — the path variable is used directly, eliminating the need for the Phase 1.1 validation.

- [x] **2.3 — Move Item-specific value objects from `common` to `Items`**
  - **Files:** `common/.../valueobject/ItemId.java`, `ItemName.java`, `ItemDescription.java`, `Quantity.java`
  - **Problem:** Items-bounded-context-specific VOs leaked into `common` library.
  - **Fix:** Relocated VOs to `Items/src/main/java/com/onlineshop/items/domain/valueobject/`. Updated all 12 import references across Items service. Removed the 4 files from `common`. Kept `BaseId`, `BaseEntity`, `AggregateRoot`, `DomainEvent`, `BaseDomainEvent` in `common`.

- [x] **2.4 — Extract `toResponse()` mappers from use cases**
  - **Files:** All use case classes in `application/usecase/`, `application/dto/mapper/ItemResponseMapper.java`
  - **Problem:** Use cases mixed orchestration with DTO mapping — duplicated `toResponse()` in 5 use cases.
  - **Fix:** Created `application/dto/mapper/ItemResponseMapper.java` with `toCreateItemResponse()`, `toUpdateItemResponse()`, `toGetItemResponse()`. Injected into all 5 use cases via constructor. Removed all private `toResponse()` methods. Updated `SearchItemsUseCaseTest` for the new constructor dependency.

### Phase 3: Domain Depth

- [x] **3.1 — Seal domain event hierarchy**
  - **Files:** `domain/event/ItemDomainEvent.java` (new), `ItemCreated.java`, `ItemUpdated.java`, `ItemDeleted.java`
  - **Problem:** Domain events extend `BaseDomainEvent` but are not part of a sealed hierarchy. A future developer adding `ItemPriceChanged` could miss updating all handlers.
  - **Fix:** Created `sealed interface ItemDomainEvent extends DomainEvent permits ItemCreated, ItemUpdated, ItemDeleted`. Updated event classes to implement `ItemDomainEvent`. Refactored `ItemDomainEventListener` to use pattern matching switch — adding a new event type will now fail to compile.

- [x] **3.2 — Fix `ItemDescription` null handling**
  - **Files:** `ItemDescription.java`, `ItemResponseMapper.java`, `ItemMapper.java`, `Item.java`, `ItemDomainEventListener.java`, `SearchItemsUseCaseTest.java`
  - **Problem:** `ItemName` rejects null; `ItemDescription` accepts it. Forces null checks in 5 different locations.
  - **Fix:** Made `ItemDescription` convert null to empty string. Added null guard in `Item` constructor. Removed all `item.getDescription() != null ? item.getDescription().value() : null` patterns — replaced with `item.getDescription().value()`. Updated test to verify empty string handling instead of null.

- [x] **3.3 — Enrich `Item` aggregate with business behavior**
  - **File:** `domain/aggregateroots/Item.java`
  - **Problem:** The aggregate only has `updateDetails()` and `markAsDeleted()`. Missing real domain logic.
  - **Fix:** Added:
    - `increaseStock(Quantity amount)` — adds stock
    - `decreaseStock(Quantity amount)` — reduces stock with invariant: cannot go below zero
    - `reserveStock(Quantity amount)` — reserves stock (delegates to decreaseStock; will track reservedQuantity separately when orders context is implemented)

- [x] **3.4 — Remove unused `@Setter` import from `BaseEntity.java`**
  - **File:** `common/.../entity/BaseEntity.java`
  - **Fix:** Removed `import lombok.Setter;` and `import lombok.EqualsAndHashCode;` (both unused).

### Phase 4: Testing

- [x] **4.1 — Add domain unit tests**
  - Test `Item` aggregate behavior: factory creation, state transitions (`updateDetails`, `markAsDeleted`), event registration
  - Test value object validation: `ItemName`, `ItemDescription`, `Quantity` boundary cases
  - Test domain event structure
  - **No Spring context** — pure JUnit 5 + AssertJ

- [x] **4.2 — Add use case integration tests**
  - Test `CreateItemUseCase`, `UpdateItemUseCase`, `DeleteItemUseCase`
  - Use `@SpringBootTest` with Testcontainers (PostgreSQL 18)
  - Verify events are published and persisted correctly

- [x] **4.3 — Add controller E2E tests (full HTTP stack)**
  - Test `ItemsController` endpoints with `@SpringBootTest(webEnvironment = RANDOM_PORT)` + Testcontainers + `RestTemplate`
  - Cover full lifecycle: create → get → update → delete → 404 after delete
  - Cover edge cases: null description, not-found error responses (404), empty search results
  - **Note:** Spring Boot 4.0.2 removed `@WebMvcTest`, `@MockBean`, and `MockMvc` from `spring-boot-test-autoconfigure`. Using `@SpringBootTest` + Testcontainers + `RestTemplate` is the only available approach.

---

## Deployment Maintenance Requirements

> **MANDATORY:** Every change in this plan that affects deployment, infrastructure, or runtime configuration MUST update the following files in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/`:

1. **`WHAT-WAS-DONE.md`** — Log every action taken: AWS resource changes, configuration updates, code changes that affect deployment, fixes applied during deployment. This file enables future automation by tracking every step needed to reproduce the environment.

2. **`scripts/resume-playground.sh`** and **`scripts/pause-playground.sh`** — If any infrastructure changes affect the pause/resume flow (new services, changed ports, new security groups, different task definitions), these scripts MUST be updated to remain functional. Verify after every infrastructure change:
   - Hardcoded IDs (VPC, subnets, security groups) are still valid
   - Service names match current ECS services
   - Target group configuration matches current API Gateway setup
   - Health check endpoints are correct

**Why:** These scripts are the foundation for cost optimization (pausing when idle saves ~$47/month). If they drift from reality, the automation breaks and manual intervention is required.

---

## Issues (Post-Implementation Tracking)

| # | Issue | Status |
|---|-------|--------|
| 1 | **No Transactional Outbox** — Events published via `ApplicationEventPublisher` with no delivery guarantee. Lost on process crash before broker gets them. | 🔴 Deferred (needs Spring Modulith or custom Outbox table) |
| 2 | **`@Data` on `BaseId` generates setters** — Minor risk for value object immutability | 🟡 Low priority |
| 3 | **No Integration Event translation layer** — Domain events carry VO payloads directly. Publishing to external broker would leak internal model. | 🔴 Deferred (needed when adding real message broker) |
| 4 | **Queries hydrate full aggregates** — `GetAllItemsUseCase`, `SearchItemsUseCase` load complete `Item` aggregates even for summaries. Could optimize with `JdbcTemplate` or read model projection. | 🟡 Low priority (deferred until performance issue) |
