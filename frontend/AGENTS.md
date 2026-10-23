# Frontend - Service Documentation

## Overview

React + TypeScript SPA for the OnlineShop e-commerce platform.

| Property   | Value                                  |
|------------|----------------------------------------|
| Port       | 5173 (dev server)                    |
| Tech Stack | React 19, TypeScript, Vite, Tailwind CSS, Axios, Zustand |
| Location   | `/frontend`                            |

## Build & Run

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

### Build
```bash
npm run build
```

## CI Verification

On every push and pull request, the independent frontend job uses Node 24 and
runs these commands from the `frontend/` module root:

```bash
npm ci
npm run lint
npm run build
```

There is no current frontend unit-test script. Separately, the root image job
builds Auth, Items, API Gateway, and frontend images with `docker compose build
auth-service items-service api-gateway frontend`; `common` is library-only.
The frontend image starts Vite development mode rather than creating a
production bundle, so the CI `npm run build` check remains required. Images are
neither published nor deployed, and this image job does not wait for Java or
frontend verification results.

Only pull requests use the image job's locally built images to start the full
Compose stack with `docker compose up -d --no-build --wait --wait-timeout 300`.
After bounded gateway and frontend readiness checks, the current three API E2E
tests run through the gateway, Auth, and Items. They do not browser-test the
frontend or directly test each infrastructure service or administrative UI.
PR E2E reports, bounded Compose status, and redacted bounded application logs
on failure are retained before `docker compose down -v --remove-orphans` is
always attempted.

## API Integration

- **Base URL**: Controlled by `VITE_API_URL` env var at build time
- **Default (local)**: `http://localhost:10000`
- **Same-origin host**: `''` (relative API URLs)
- **Client**: Axios with request/response interceptors for auth tokens and 401 handling

### Code Reference
```ts
// frontend/src/services/api.ts
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
```

**Note:** `??` preserves an empty `VITE_API_URL`, enabling relative API calls.

### Multi-Worktree (Per-Worktree Frontend)

Create non-main worktrees with the persisted root `wtc <name>` command (use
`wtc <name> false` to remain in the current checkout); it runs
`scripts/create-worktree.py` and fast-forwards the current checkout with
`git pull --ff-only` first.
The frontend's `VITE_API_URL` is then set to
`http://localhost:<GATEWAY_PORT>` by the compose file via the allocated `.env`.
The containerized frontend auto-connects to the correct worktree gateway.
The worktree command intentionally configures Compose only; it does not export
host-run Vite variables.

Repository automation must follow [Script Guidelines](../docs/SCRIPT_GUIDELINES.md).

## SPA Routing

React Router handles client-side navigation. Configure a static host to serve `/index.html` for unknown routes so direct links work.

## Key Dependencies

- `axios` — HTTP client
- `zustand` — State management (auth store)
- `react-router` — Client-side routing
- `react-hook-form` + `zod` — Form handling and validation

## UI Behavior and Verification

The catalog filters locally loaded items by name/description and can toggle an
in-stock-only view. Product details remain reachable for out-of-stock items;
only the purchase action is disabled because the cart API is not implemented.
The catalog distinguishes an API failure from a successful empty response and
offers a retry action.

After frontend changes, run the checks inside the running Compose frontend
container (the worktree may use allocated host ports rather than 5173):

```bash
docker compose exec -T frontend npm run lint
docker compose exec -T frontend npm run build
```

Manual browser smoke flow:

```text
home → login failure/success → catalog search/filter → out-of-stock details
     → protected deep link → login → original detail route → logout
```

Check mobile widths at 320px, 375px, and 414px for horizontal overflow, and
respect `prefers-reduced-motion` when reviewing loading and hover animations.
The optional screenshot helper accepts allocated worktree URLs instead of
assuming the default port:

```bash
FRONTEND_URL="http://127.0.0.1:$(docker compose port frontend 5173 | awk -F: '{print $NF}')" \
  SCREENSHOT_PATH=/tmp/onlineshop.png npm run screenshot
```
