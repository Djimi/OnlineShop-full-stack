# Auth Service DB Queries Investigation

Environment used:
- `auth-postgres` + `auth-service` from `docker-compose.yml`
- Endpoints called directly on `http://localhost:9001`
- Test flow: `register -> login -> validate`
- Captured from: `docker compose logs auth-service --since "2026-02-08T22:40:58Z" --until "2026-02-08T22:41:10Z"`

## Register (`POST /api/v1/auth/register`)

### Queries (copied from terminal)
```sql
select
    u1_0.id
from
    users u1_0
where
    u1_0.normalized_username=?
fetch
    first ? rows only
```

```sql
insert
into
    users
    (created_at, normalized_username, password_hash, username)
values
    (?, ?, ?, ?)
```

### Why Hibernate executes them
- `existsByNormalizedUsername(normalizedUsername)` in `AuthService.register(...)` triggers the existence check query.
- Spring Data JPA optimizes `exists...` into a limited select (`fetch first ? rows only`) because it only needs to know whether at least one row exists.
- `userRepository.save(user)` persists a new `User` entity, producing the `insert` into `users`.
- `@PrePersist` in `User` sets `normalized_username`; `@CreationTimestamp` sets `created_at` before insert.

## Login (`POST /api/v1/auth/login`)

### Queries (copied from terminal)
```sql
select
    u1_0.id,
    u1_0.created_at,
    u1_0.normalized_username,
    u1_0.password_hash,
    u1_0.updated_at,
    u1_0.username
from
    users u1_0
where
    u1_0.normalized_username=?
```

```sql
insert
into
    sessions
    (created_at, expires_at, token_hash, user_id)
values
    (?, ?, ?, ?)
```

### Why Hibernate executes them
- `userRepository.findByNormalizedUsername(...)` in `AuthService.login(...)` loads the full `User` entity for password verification, so Hibernate selects all mapped user columns.
- After password verification succeeds, `sessionRepository.save(session)` inserts a new `Session` row with `token_hash`, `user_id`, and `expires_at`.
- `created_at` in `sessions` is populated from entity state (`@CreationTimestamp`) during persist.

## Validate Token (`GET /api/v1/auth/validate`)

### Queries (copied from terminal)
```sql
select
    s1_0.id,
    s1_0.created_at,
    s1_0.expires_at,
    s1_0.token_hash,
    s1_0.user_id
from
    sessions s1_0
where
    s1_0.token_hash=?
```

```sql
select
    u1_0.id,
    u1_0.created_at,
    u1_0.normalized_username,
    u1_0.password_hash,
    u1_0.updated_at,
    u1_0.username
from
    users u1_0
where
    u1_0.id=?
```

### Why Hibernate executes them
- `sessionRepository.findByTokenHash(tokenHash)` in `AuthService.validateToken(...)` loads the matching `Session`.
- `Session.user` is mapped `@ManyToOne(fetch = FetchType.LAZY)`, so user data is not joined in the first query.
- When `validateToken(...)` builds the response and accesses `session.getUser().getId()/getUsername()`, Hibernate initializes the lazy association and executes the second query by `user_id`.

## Notes
- SQL appears with `?` placeholders because bind value logging is not enabled for Hibernate 7 in current config (the configured logger key is legacy).
- Each query above is exactly what appeared in terminal logs for this run.
