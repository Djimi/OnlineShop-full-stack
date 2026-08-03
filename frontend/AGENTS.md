# Frontend - Service Documentation

## Overview

React + TypeScript SPA for the OnlineShop e-commerce platform.

| Property   | Value                                  |
|------------|----------------------------------------|
| Port       | 5173 (dev server)                    |
| Tech Stack | React 19, TypeScript, Vite, Tailwind CSS, Axios, Zustand |
| Location   | `/frontend`                            |

## Build & Deploy

### Docker Compose
```bash
# Run from the repository root
docker compose up -d --build frontend
```

The frontend Dockerfile installs npm dependencies and copies the current source into a Vite development container. The Compose source mount keeps `frontend/src` live during development; rebuild after changing `package.json` or the lockfile. `docker compose up -d --build` rebuilds and starts the complete stack, including the frontend.

### Local Development
```bash
cd frontend
npm install
npm run dev
```

### Production Build
```bash
# For local Docker / same-origin deployment
npm run build

# For AWS CloudFront deployment (relative API URLs)
VITE_API_URL='' npm run build
```

## CI/CD

The current Java-service CI pipeline does not build or publish the frontend. Frontend changes require the frontend-specific build and deployment process documented above.

### Deploy to AWS
```bash
# Build
VITE_API_URL='' npm run build

# Upload to S3
aws s3 sync dist/ s3://onlineshop-frontend-799111666795/ --delete --profile dpm-profile --region eu-north-1

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id EPS8MI3FV3B7X --paths "/*" --profile dpm-profile --region us-east-1
```

## API Integration

- **Base URL**: Controlled by `VITE_API_URL` env var at build time
- **Default (local)**: `http://localhost:10000`
- **Production (CloudFront)**: `''` (relative URLs, same-origin through CloudFront)
- **Client**: Axios with request/response interceptors for auth tokens and 401 handling

### Code Reference
```ts
// frontend/src/services/api.ts
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
```

**Note:** Uses `??` (nullish coalescing) instead of `||` so that `VITE_API_URL=''` is preserved as an empty string (enabling relative API calls).

### Multi-Worktree (Per-Worktree Frontend)

In a non-main worktree, the frontend's `VITE_API_URL` is set to `http://localhost:<GATEWAY_PORT>` by the compose file (via `.env`). The containerized frontend auto-connects to the correct worktree gateway.

**Host-run frontend in a worktree:** Always source the exports before running Vite:

```bash
source <(scripts/dev-env.sh --exports)
npm run dev -- --port "$FRONTEND_PORT"
```

**WARNING:** Without `--exports`, `VITE_API_URL` is unset and `api.ts` falls back to `http://localhost:10000` — main's gateway. This is a silent cross-worktree data-plane mixup, worse than a crash.

## SPA Routing

React Router handles client-side navigation. CloudFront is configured with a custom error response:
- `404` → `200` with `/index.html`

This allows direct navigation to routes like `/login` and `/items` without server-side support.

## Key Dependencies

- `axios` — HTTP client
- `zustand` — State management (auth store)
- `@tanstack/react-query` — Server state management
- `react-router` — Client-side routing
- `react-hook-form` + `zod` — Form handling and validation

## AWS CLI Conventions

For AWS CLI commands (infrastructure queries, deployments, etc.), see the root [AGENTS.md](../AGENTS.md) — all AWS commands MUST include `--profile dpm-profile --region eu-north-1`.
