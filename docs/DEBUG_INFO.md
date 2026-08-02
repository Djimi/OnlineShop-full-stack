# Debug Info

> **Important:** When making code changes to backend services, you MUST:
> 1. Build the JAR with Maven from the service directory (`./mvnw clean package`)
> 2. Stop the running service (`docker compose down <service-name>`)
> 3. Rebuild the Docker image from root (`docker compose up -d --build <service-name>`)
>
> Without step 1, the Docker image will use the old JAR and changes won't apply.

## Multi-Worktree Port Conflicts

If `docker compose up` fails with a bind error (port already in use):

```bash
# Check which worktree's ports you have
scripts/dev-env.sh

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

**Forgot to run dev-env.sh?** A bare `docker compose up` in a non-main worktree without running `scripts/dev-env.sh` will use main's ports — colliding with the main checkout if it's running. Run `scripts/dev-env.sh` first.

See [docs/MULTI_WORKTREE.md](./MULTI_WORKTREE.md) for full multi-worktree guide.


## Essential Commands

> **Note:** All `docker compose` commands must be run from the root project directory.

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# Stop all services AND remove volumes (DATABASE DATA WILL BE LOST)
# DANGER: Only run when explicitly requested and confirmed by user
docker compose down -v

# Apply code changes to a service (MUST build JAR first, then rebuild container)
# Run from service directory (e.g., Items/, Auth/, api-gateway/, etc):
./mvnw clean package -DskipTests
# Then rebuild the Docker image:
docker compose up -d --build <service-name>

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

# Trigger workflow manually (only works after merging to main)
gh workflow run "Build & Push to ECR" -f service=all

# View workflow run logs
gh run view <run-id> --log

# List recent workflow runs
gh run list --workflow="Build & Push to ECR" --limit 5

# Check gh CLI auth status
gh auth status
```
