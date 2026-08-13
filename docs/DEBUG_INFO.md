# Debug Info

> **Important:** Java service Dockerfiles build from source in Docker. When making code changes, rebuild and restart the affected service from the repository root:
>
> ```bash
> docker compose up -d --build <service-name>
> ```
>
> A host-side Maven package and manual `docker compose down` are not required. Docker reuses unchanged layers and includes changes from the service build context.
>
> `docker build` builds one image; use `docker compose up -d --build` when you want to rebuild and start the complete stack.

## Multi-Worktree Port Conflicts

New worktrees must be created with the atomic creation command:

```bash
scripts/create-worktree.sh <path-or-name> -b <new-branch> [base-ref]
```

If an external application takes a port after allocation and `docker compose
up` reports a bind error:

```bash
# Validate the complete claim and check for another worktree using the slot
scripts/dev-env.sh --check

# Regenerate with a new slot (downs old stack first)
scripts/dev-env.sh --regenerate
docker compose up -d --build

# To also drop DB volumes when regenerating:
scripts/dev-env.sh --regenerate --volumes
```

**Manual debug:** find what's using a port:
```bash
ss -tlnH "sport = :<port>"    # shows the process listening on <port>
docker ps --filter publish=<port>  # shows the container
```

**Missing `.env` claim?** The worktree was not created through the current
wrapper, or allocation failed after Git created it. Run `scripts/dev-env.sh`
inside that worktree to recover. The allocator fails closed when another
worktree already owns the candidate slot.

See [docs/MULTI_WORKTREE.md](./MULTI_WORKTREE.md) for full multi-worktree guide.


## Essential Commands

> **Note:** All `docker compose` commands must be run from the root project directory.

```bash
# Build current application sources and start all services
docker compose up -d --build

# Stop all services
docker compose down

# Stop all services AND remove volumes (DATABASE DATA WILL BE LOST)
# DANGER: Only run when explicitly requested and confirmed by user
docker compose down -v

# Apply code changes to a service (run from the repository root)
docker compose up -d --build <service-name>

# Host-side package is optional and is not needed by Docker Compose:
# cd <service-directory> && ./mvnw clean package -DskipTests

# Run unit + integration tests (from service directory)
./mvnw clean test

# Run application with local profile without docker containers.
./mvnw spring-boot:run -Dspring-boot.run.profiles=local

# Run e2e tests (from e2e-tests/, requires docker compose up first)
cd e2e-tests && ./mvnw clean test

# Frontend development
cd frontend && npm run dev

# Frontend build
cd frontend && npm run build
```
> **Important:** If you ran into an issue that container is existing, check whether it was created by that docker compose file or not. If not, you need to remove it manually using `docker rm <container-id>` command.

---

## CI/CD & AWS Debugging

```bash
# Verify who you are in AWS (always run first)
aws sts get-caller-identity

# Set the region
aws configure set region eu-north-1

# Check if GitHub OIDC provider is configured
aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[*].Arn"

# Verify OIDC provider details (audience, thumbprint)
aws iam get-open-id-connect-provider --open-id-connect-provider-arn "arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com"

# List IAM roles matching a pattern
aws iam list-roles --query "Roles[?contains(RoleName,'github')].{Name:RoleName,Arn:Arn}"

# Check role policies
aws iam list-role-policies --role-name github-actions-onlineshop

# Check ECR repositories (must specify region)
aws ecr describe-repositories --region eu-north-1

# Check if an ECR repo has images
aws ecr describe-images --repository-name onlineshop-auth --region eu-north-1

# Verify pushed images in ECR
aws ecr describe-images --repository-name onlineshop-auth --region eu-north-1 --query "imageDetails[*].imageTags[0]"

# Trigger the CI/CD workflow manually (after it is available from main)
gh workflow run "CI/CD Pipeline" -f service=all

# View workflow run logs
gh run view <run-id> --log

# List recent workflow runs
gh run list --workflow="CI/CD Pipeline" --limit 5

# Check gh CLI auth status
gh auth status
```
