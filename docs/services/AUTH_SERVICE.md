# Auth Service

## Overview

| Property   | Value                                       |
|------------|---------------------------------------------|
| Port       | 9001                                        |
| Tech Stack | Spring Boot 4, Spring Security, PostgreSQL  |
| Location   | `/Auth`                                     |
| Database   | PostgreSQL (auth) on port 5433              |

## Responsibilities

1. **User Registration** - Create new user accounts
2. **Authentication** - Validate credentials, issue tokens
3. **Token Validation** - Validate tokens for API Gateway
4. **Session Management** - Track active sessions
5. **Password Management** - (Future) Reset, change password

## Key Files

| Purpose         | Location                                               |
|-----------------|--------------------------------------------------------|
| Configuration   | `Auth/src/main/resources/application.yml`              |
| DB Schema       | `Auth/init-db/01-schema.sql`                           |
| Seed Data       | `Auth/init-db/02-seed-data.sql`                        |
| Auth Controller | `Auth/src/main/java/.../controller/AuthController.java` |
| Auth Service    | `Auth/src/main/java/.../service/AuthService.java`      |

## Database Schema

See `Auth/init-db/01-schema.sql` for full schema. Main tables:

- **users** - User accounts (username, password_hash, timestamps)
- **sessions** - Active sessions (user_id, token_hash, expires_at)

## API Endpoints

### POST /api/v1/auth/register
Register a new user. Returns user info.

### POST /api/v1/auth/login
Authenticate and receive opaque token.

### GET /api/v1/auth/validate
Validate token (used by API Gateway). Returns user info if valid.

### POST /api/v1/auth/refresh
Refresh an expiring token.

### POST /api/v1/auth/logout
Invalidate current session.

## Security

### Token Strategy: Opaque Tokens (not JWT)

Tokens are **opaque** - random 32-byte hex strings with no embedded data:
- Generated using `SecureRandom`
- Stored as SHA-256 hash in database (never plain)
- Validation requires database lookup
- Revocable immediately (delete session row)

This differs from JWT where token contains claims. Trade-off: database lookup required, but simpler revocation.

### Password Hashing
BCrypt with Spring Security's `PasswordEncoder`.

## Running Locally

### With Docker Compose (Recommended)
```bash
docker compose up -d --build auth-service auth-postgres
```

`Auth/Dockerfile` builds the service from source in a Maven build stage, so a host-side `target/*.jar` is not required. From the repository root, `docker compose up -d --build` rebuilds and starts the complete stack.

### Standalone
```bash
cd Auth
JAVA_HOME="/c/Program Files/Java/jdk-25" ./mvnw.cmd spring-boot:run
```

Requires PostgreSQL on port 5433.

## Health Check

```bash
curl http://localhost:9001/actuator/health
```

## Testing

```bash
cd Auth
./mvnw.cmd test
```

## Common Issues

### Connection Refused to PostgreSQL
1. Check PostgreSQL is running: `docker compose ps auth-postgres`
2. Verify port 5433 is correct
3. Check credentials in `application.yml`

### Token Validation Fails
1. Check token hasn't expired (see `session.expiration` config)
2. Verify session exists in database
3. Check token is passed correctly (with "Bearer " prefix per RFC 6750)
