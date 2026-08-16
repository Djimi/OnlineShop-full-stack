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

Pull requests and feature-branch pushes run `npm ci`, `npm run lint`, and
`npm run build` when frontend paths change. The pipeline validates the frontend
but does not publish it; AWS publication remains a separate deployment step.
The frontend validation is a required `main` branch-protection check.

The Pass 3 candidate-evidence job (`.github/workflows/build-and-deploy.yml`)
additionally builds the canonical candidate with `VITE_API_URL=''`, packages
`dist` reproducibly into `frontend-dist.tar.gz` (see
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/package-frontend.sh`), generates
a sorted per-file checksum manifest plus the archive SHA-256, and emits a
`frontend.spdx.json` SBOM — all retained with the candidate evidence for the
later promotion/rollback phases.

Frontend releases will be deployed under immutable S3 release prefixes
(`_releases/v<version>/`) with a `release.json` version marker; the promotion
preflight (`release/bin/check-release-identity.sh`) fails closed on any prefix
collision and resumes only when an existing marker exactly matches the
validated manifest. Pass 3R.1 makes the handoff explicit: promotion consumes
the candidate archive plus the actual production snapshot, publishes assets
before the live marker, and renders an official manifest only from the
deployment manifest after verification. `publish-frontend.sh`,
`restore-frontend.sh`, and promotion compensation request
`--checksum-algorithm SHA256` on every S3 write. Snapshot requires the live
index's full-object `ChecksumSHA256` and the matching immutable prefix; an ETag
is never accepted as a SHA-256 substitute. The role cutover is Pass 3R.9 and
the live promotion/rollback proof is Pass 3R.10.

### Deploy to AWS
```bash
# Build
VITE_API_URL='' npm run build

# Upload to S3 (manual root sync only; release promotion uses publish-frontend.sh)
# Keep old hashed assets and request a service-reported SHA-256 checksum.
aws s3 sync dist/ s3://onlineshop-frontend-799111666795/ --checksum-algorithm SHA256 --profile dpm-profile --region eu-north-1

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id EPS8MI3FV3B7X --paths "/*" --profile dpm-profile --region eu-north-1
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

Create non-main worktrees with the root `scripts/create-worktree.py` command.
The frontend's `VITE_API_URL` is then set to
`http://localhost:<GATEWAY_PORT>` by the compose file via the allocated `.env`.
The containerized frontend auto-connects to the correct worktree gateway.
The worktree command intentionally configures Compose only; it does not export
host-run Vite variables.

Repository automation must follow [Script Guidelines](../docs/SCRIPT_GUIDELINES.md).

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

Repository pause/resume scripts log UTC timestamped steps, typical durations,
resource-level AWS progress, and actual total runtime. Treat duration values as
operational estimates, not timeout guarantees.

## Pass 3.5 — Production hardening

The production frontend delivery is **defined** by the hardening contract (see
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/PRODUCTION-HARDENING-DECISIONS.md`).
The current live delivery still uses the v1 S3 website origin; the migration
below is tooling that has **not been applied live in 3.5**:

- The v1 S3 **website** origin + public-read policy is to be replaced by an S3
  **REST** origin behind a CloudFront **Origin Access Control** with the bucket
  public access block fully enabled and the SPA fallback (404 → 200
  `/index.html`) preserved through CloudFront. `scripts/verify-frontend-oac.sh`
  is the read-only fail-closed check; `scripts/migrate-frontend-oac.sh` is the
  mutation tool (per-step read-back, fail closed). The apply run starts with a
  **no-lockout precondition gate** (the current bucket policy must already
  grant public read or the CloudFront OAC) and waits for the asynchronous
  CloudFront deployment to reach `Deployed` before tightening the bucket policy.
  **Not applied live in 3.5** — application is deferred to the consolidated
  Pass 3 verification pass.
- `VITE_API_URL=''` builds keep API calls same-origin through CloudFront.
- The bucket `onlineshop-frontend-799111666795`, CloudFront distribution
  `EPS8MI3FV3B7X`, and the immutable `_releases/v<version>/` prefix are the
  explicit non-secret frontend identifiers recorded in
  `scripts/config/production.env`.

## Pass 3.7 — Release traceability

The read-only release traceability queries (`release/bin/trace.sh` +
`release_contract.traceability`) resolve the frontend identity from the
deployed immutable `release.json` version marker (`version`/`sourceSha`/
`frontendSha256`), never cache headers: `release --version` returns the
frontend artifact/checksum/prefix and cross-checks the live marker,
`running` reports the deployed frontend marker version/checksum, and `audit`
cross-checks the deployed live marker and each release's immutable
`_releases/v<version>/release.json` prefix marker. The offline gate is
`bash tests/scripts/release_traceability_test.sh`; live lookups against real
AWS are deferred to the consolidated verification pass.
