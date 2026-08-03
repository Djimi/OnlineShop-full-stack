# Frontend Service

## Overview

| Property   | Value                                     |
|------------|-------------------------------------------|
| Port       | 5173 (dev)                                |
| Tech Stack | React 19, TypeScript, Vite, Tailwind CSS  |
| Location   | `/frontend`                               |
| API        | Connects to API Gateway on port 10000     |

## Responsibilities

1. **User Interface** - Responsive web application
2. **Authentication** - Login, register, token management
3. **Product Catalog** - Browse and view items
4. **Shopping** - (Future) Cart, checkout

## Key Files

| Purpose         | Location                          |
|-----------------|-----------------------------------|
| Vite Config     | `frontend/vite.config.ts`         |
| Tailwind Config | `frontend/tailwind.config.js`     |
| TypeScript      | `frontend/tsconfig.json`          |
| Entry Point     | `frontend/src/main.tsx`           |
| API Services    | `frontend/src/services/`          |
| Auth Store      | `frontend/src/stores/authStore.ts` |

## Project Structure

```
frontend/src/
├── components/     # Reusable UI components
├── pages/          # Route pages
├── services/       # API service calls
├── stores/         # State management (Zustand)
├── hooks/          # Custom React hooks
├── types/          # TypeScript definitions
└── utils/          # Utility functions
```

## Pages

| Route          | Component    | Auth Required |
|----------------|--------------|---------------|
| `/`            | Home         | No            |
| `/login`       | Login        | No            |
| `/register`    | Register     | No            |
| `/items`       | Items        | Yes           |
| `/items/:id`   | ItemDetail   | Yes           |

## State Management

- **Auth state:** Zustand store with localStorage persistence
- **Server state:** React Query for API data

## Development

### With Docker Compose (Recommended)
```bash
# Run from the repository root
docker compose up -d --build frontend
```

The frontend Dockerfile installs npm dependencies and copies the current source into the Vite development container. The Compose source mount keeps `frontend/src` live during development; rebuild after changing `package.json` or the lockfile. Use `docker compose up -d --build` to rebuild and start the complete stack.

### Start Dev Server
```bash
cd frontend
npm install
npm run dev
```

Opens at: http://localhost:5173

### Build
```bash
npm run build
```

### Lint
```bash
npm run lint
```

### Type Check
```bash
npx tsc --noEmit
```

## Common Issues

### CORS Errors
1. Ensure API Gateway is running on port 10000
2. Check requests go through gateway, not direct to services
3. Verify origin is in gateway's allowed list

### Token Not Being Sent
1. Check token is in localStorage
2. Verify axios interceptor is configured
3. Check Authorization header in Network tab

### Page Refresh Loses Auth
1. Check authStore initializes from localStorage
2. Verify token validation on app load
