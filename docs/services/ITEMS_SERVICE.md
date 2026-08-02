# Items Service

## Overview

| Property   | Value                                       |
|------------|---------------------------------------------|
| Port       | 9000                                        |
| Tech Stack | Spring Boot 4, Spring Data JPA, PostgreSQL  |
| Location   | `/Items`                                    |
| Database   | PostgreSQL (items) on port 5432             |

## Responsibilities

1. **Item CRUD** - Create, read, update, delete items
2. **Catalog Browsing** - List items with pagination
3. **Search** - (Future) Full-text search with filters
4. **Inventory** - (Future) Stock management

## Key Files

| Purpose         | Location                                                |
|-----------------|---------------------------------------------------------|
| Configuration   | `Items/src/main/resources/application.yml`              |
| DB Schema       | `Items/init-db/01-schema.sql`                           |
| Seed Data       | `Items/init-db/02-data.sql`                             |
| Controller      | `Items/src/main/java/.../controller/ItemController.java` |
| Service         | `Items/src/main/java/.../service/ItemService.java`      |
| DTOs            | `Items/src/main/java/.../dto/` (ItemRequest, ItemResponse) |
| Entity          | `Items/src/main/java/.../domain/Item.java`              |

## Database Schema

Source: `Items/init-db/01-schema.sql`

## API Endpoints

> All endpoints require authentication via API Gateway.

| Method | Path               | Description              | Request DTO   | Response DTO   |
|--------|--------------------|--------------------------|---------------|----------------|
| GET    | `/api/v1/items`    | List items (paginated)   | -             | Page<ItemResponse> |
| GET    | `/api/v1/items/{id}` | Get single item        | -             | ItemResponse   |
| POST   | `/api/v1/items`    | Create item              | ItemRequest   | ItemResponse   |
| PUT    | `/api/v1/items/{id}` | Update item            | ItemRequest   | ItemResponse   |
| DELETE | `/api/v1/items/{id}` | Delete item            | -             | -              |

Query params for GET list: `page`, `size`, `category`

## Running Locally

### With Docker Compose
```bash
docker compose up -d --build items-service items-postgres
```

`Items/Dockerfile` uses the repository root as its build context, installs the `common` library, and builds Items from source in Docker. A host-side `target/*.jar` is not required. From the repository root, `docker compose up -d --build` rebuilds and starts the complete stack.

### Standalone
```bash
cd Items
JAVA_HOME="/c/Program Files/Java/jdk-25" ./mvnw.cmd spring-boot:run
```

## Testing

```bash
cd Items
./mvnw.cmd test
```

## Common Issues

### Connection to PostgreSQL Fails
1. Check PostgreSQL is running: `docker compose ps items-postgres`
2. Check credentials in `application.yml`

### Slow Queries
1. Check indexes on frequently queried columns
2. Review query execution plans
