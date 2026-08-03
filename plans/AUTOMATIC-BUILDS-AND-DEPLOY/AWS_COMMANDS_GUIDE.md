# Step 1.3 Guide — AWS, GitHub Actions, and Everything We Learned

> **Goal:** This document walks through **every command and every issue** we encountered while setting up the automated build-and-push workflow. Each section explains **what** we did, **why** we did it, **what went wrong**, and **how we fixed it**. It's written for someone who is new to both AWS and GitHub Actions.
>
> **AWS Account ID:** `799111666795` | **Region:** `eu-north-1` (Stockholm) | **Repo:** `Djimi/OnlineShop-claude`

---

## Table of Contents

- [Part A: AWS Commands (IAM, OIDC, ECR)](#part-a-aws-commands-iam-oidc-ecr)
  - [A1. Identity & Context](#a1-identity--context)
  - [A2. OIDC Provider — "How GitHub Talks to AWS Without Passwords"](#a2-oidc-provider--how-github-talks-to-aws-without-passwords)
  - [A3. IAM Roles & Policies — "Who Can Do What"](#a3-iam-roles--policies--who-can-do-what)
  - [A4. ECR — "Where Docker Images Live"](#a4-ecr--where-docker-images-live)
- [Part B: GitHub Actions Workflow — Full Issue Log](#part-b-github-actions-workflow--full-issue-log)
  - [B1. Commit-by-Commit History](#b1-commit-by-commit-history)
  - [B2. Issue Deep-Dives](#b2-issue-deep-dives)
- [Part C: Quick Reference](#part-c-quick-reference)

---

# Part A: AWS Commands (IAM, OIDC, ECR)

## A1. Identity & Context

### `aws sts get-caller-identity`

```bash
aws sts get-caller-identity
```

| Field | Meaning |
|-------|---------|
| `UserId` | Unique ID of the IAM user/role you're authenticated as |
| `Account` | The 12-digit AWS account ID |
| `Arn` | Full identity — tells you if you're a human user or an assumed role |

**Why we ran this:** Before doing anything in AWS, you need to confirm **who you are** and **which account** you're in. This command has zero side effects — it's 100% safe to run anytime.

**What happened:** We got back `arn:aws:iam::799111666795:user/admin` — we're logged in as the `admin` user in account `799111666795`. Confirmed.

**Why it matters:** If this returned a different account ID, every subsequent command would be targeting the wrong account, potentially creating resources you'd never find.

> **Tip:** Always run this first when you open a new terminal. It takes 0.5 seconds and prevents hours of confusion.

---

## A2. OIDC Provider — "How GitHub Talks to AWS Without Passwords"

### What problem does OIDC solve?

Normally, if a script (running on GitHub Actions) wants to talk to AWS, you'd create an **AWS Access Key + Secret Key** and store them as GitHub secrets. This has two problems:

1. **Secrets can leak** — if someone gets the key, they can use it from anywhere
2. **Secrets never expire** — you have to manually rotate them

**OIDC (OpenID Connect)** is the modern alternative. Instead of storing AWS keys in GitHub:

```
GitHub Actions workflow
    ↓ (1) Asks GitHub: "Give me a signed identity token proving I'm running in repo Djimi/OnlineShop-claude"
    ↓ (2) Sends that token to AWS
AWS IAM
    ↓ (3) Checks: "Is this token signed by GitHub? Is it for the right repo?"
    ↓ (4) If yes, returns TEMPORARY AWS credentials (expire after 1 hour)
```

**Why this is better:**
- No long-lived secrets to leak
- Tokens are automatically scoped to the specific repo + branch
- AWS credentials auto-expire after ~1 hour

### What we needed to verify

The OIDC provider was set up **before our session** (in step 1.1 of the plan). We needed to check that it was configured correctly. A misconfigured OIDC provider would make the entire GitHub → AWS auth flow silently fail.

### `aws iam list-open-id-connect-providers`

```bash
aws iam list-open-id-connect-providers

# With filter (JMESPath)
aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[*].Arn"
```

**Why we ran this:** To check if the GitHub OIDC provider is registered in our AWS account. Each AWS account can have multiple OIDC providers (GitHub, GitLab, Google, etc.).

**What happened:** We got back one provider: `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com`. This confirms the GitHub Actions OIDC identity provider exists.

### `aws iam get-open-id-connect-provider`

```bash
aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com"
```

**Why we ran this:** Just knowing the provider exists isn't enough — we need to check its **configuration details** (audience and thumbprint).

| Field | What it means | Value we got | Is it correct? |
|-------|--------------|-------------|----------------|
| `Url` | The identity provider's URL | `token.actions.githubusercontent.com` | Yes |
| `ClientIDList` | The "audience" — who the token is for. GitHub Actions tokens include `aud: sts.amazonaws.com` | `["sts.amazonaws.com"]` | Yes |
| `ThumbprintList` | SHA-1 fingerprint of GitHub's TLS certificate, used to verify token signatures | `["22ff89586561fc2d52f77491e9f1eff1b80be33e"]` | Yes (matches GitHub's certificate) |

**What happened:** All three values were correct. The OIDC trust foundation is solid.

**What could go wrong here:**
- If `ClientIDList` was `["sigstore"]` instead of `["sts.amazonaws.com"]` → the audience wouldn't match, and `AssumeRoleWithWebIdentity` would fail
- If the thumbprint was outdated (GitHub rotated their TLS cert) → tokens would fail signature validation
- If no OIDC provider existed at all → we'd need to create one first

---

## A3. IAM Roles & Policies — "Who Can Do What"

### Background: IAM User vs IAM Role

| | IAM User | IAM Role |
|---|---|---|
| Has permanent credentials? | Yes (access key + secret key) | No (credentials are temporary, auto-expire) |
| Who uses it? | Humans, long-lived services | AWS services, external identities (GitHub, another AWS account) |
| How do you get in? | Log in with password or access key | "Assume" the role via a trust relationship |
| Security risk | Key can leak → attacker has permanent access | No permanent key to leak. Even if token leaks, it expires in ~1 hour |

For GitHub Actions, we **must** use an IAM Role (not a User) because OIDC only works with roles.

### The anatomy of an IAM Role

An IAM Role has **two separate things** you need to configure:

1. **Trust Policy** (who can assume this role?) — "GitHub Actions from repo `Djimi/OnlineShop-claude` can assume this role"
2. **Permissions Policies** (what can this role do?) — "Push/pull Docker images from ECR"

```mermaid
IAM Role: github-actions-onlineshop
├── Trust Policy (inbound)
│   └── "Allow sts:AssumeRoleWithWebIdentity if:
│        - The token comes from GitHub's OIDC provider
│        - The token's audience is sts.amazonaws.com
│        - The token's subject matches repo:Djimi/OnlineShop-claude:*"
└── Permissions Policy (outbound) — "ecr-push-pull"
    └── "Allow: GetAuthorizationToken, PutImage, UploadLayerPart, ..."
```

### The Trust Policy we wrote

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:Djimi/OnlineShop-claude:*"
      }
    }
  }]
}
```

**In plain English, line by line:**

| Line | Meaning |
|------|---------|
| `"Federated": "arn:...oidc-provider/token.actions.githubusercontent.com"` | "Only the GitHub OIDC provider can vouch for this role assumption" |
| `"Action": "sts:AssumeRoleWithWebIdentity"` | "The specific AWS API call being allowed is role assumption via web identity (OIDC)" |
| `"aud": "sts.amazonaws.com"` | "The GitHub token must declare it's intended for AWS STS" |
| `"sub": "repo:Djimi/OnlineShop-claude:*"` | "ONLY workflows from YOUR repo can assume this role. The `*` means any branch, any tag, any ref." |

> **Security note:** If the `sub` condition was `repo:*` (any repo), anyone with a GitHub Actions workflow could assume our role. The `repo:Djimi/OnlineShop-claude:*` scope is critical.

### `aws iam list-roles` — checking what already exists

```bash
aws iam list-roles --query "Roles[?contains(RoleName,'github') || contains(RoleName,'onlineshop')].{Name:RoleName,Arn:Arn}"
```

**Why we ran this:** Before creating a new role, check if one already exists. We searched for any role name containing "github" or "onlineshop".

**What happened:** Empty result `[]` — no roles exist yet. We need to create one from scratch.

> **The `--query` parameter** uses [JMESPath](https://jmespath.org/) syntax. It's like `jq` for AWS CLI output. Without it, `list-roles` returns up to 1000 roles in a massive JSON blob — impossible to read. The query filters by name substring and reshapes the output.

### `aws iam create-role`

```bash
aws iam create-role \
  --role-name github-actions-onlineshop \
  --assume-role-policy-document "file://trust-policy.json" \
  --description "OIDC role for GitHub Actions in OnlineShop repo"
```

**Why we ran this:** Creates the role with the trust policy. This is step 1 of 2 — the role now exists but has **no permissions yet**.

**Key output fields:**

| Field | Value | What it means |
|-------|-------|---------------|
| `RoleName` | `github-actions-onlineshop` | This name goes everywhere — workflow YAML, console, other policies |
| `Arn` | `arn:aws:iam::799111666795:role/github-actions-onlineshop` | The **full identity**. We paste this into the GitHub workflow YAML so GitHub knows which role to assume |
| `RoleId` | `AROA3UDWELRVWNA6I5JLH` | Internal AWS ID, rarely needed — but useful for CloudTrail audit logs |

### Issue: "The specified value for assumeRolePolicyDocument is invalid"

**What happened:** Our first attempt to create the role failed with this error.

**Why:** The JSON file was written with PowerShell's `@'...'@` here-string, which introduced non-printable characters (possibly a BOM — Byte Order Mark) into the UTF-8 file. AWS IAM requires the policy document to be **pure ASCII**.

**Fix:** We rewrote the file using `[System.IO.File]::WriteAllText(...)` with `[System.Text.Encoding]::ASCII`:

```powershell
$policy = '{"Version":"2012-10-17",...}'
[System.IO.File]::WriteAllText("$env:TEMP\trust-policy.json", $policy, [System.Text.Encoding]::ASCII)
```

**Lesson:** When writing JSON for AWS CLI on Windows, always use ASCII encoding. PowerShell's default UTF-8-with-BOM confuses AWS IAM.

### The ECR Permissions Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:GetRepositoryPolicy",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
      "ecr:DescribeImages",
      "ecr:BatchGetImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage"
    ],
    "Resource": "*"
  }]
}
```

**Actions grouped by what they do:**

| Group | Actions | Why needed |
|-------|---------|-------------|
| Auth | `ecr:GetAuthorizationToken` | Get a temporary Docker login password to ECR |
| Push | `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage` | Upload Docker image layers to ECR — these are the 4 steps of a docker push |
| Pull | `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage` | Download Docker image layers from ECR — needed for deployments |
| Read | `ecr:DescribeRepositories`, `ecr:ListImages`, `ecr:DescribeImages`, `ecr:GetRepositoryPolicy` | Inspect repository contents, useful for debugging and tools |

> **Why `"Resource": "*"`?** This means "all ECR repositories in this account." For production, you'd scope it to specific repo ARNs (e.g., `arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-auth`). For Pass 1 MVP, broad permissions are simpler. We'll tighten this in Pass 3.

### `aws iam put-role-policy`

```bash
aws iam put-role-policy \
  --role-name github-actions-onlineshop \
  --policy-name ecr-push-pull \
  --policy-document "file://ecr-policy.json"
```

**Why we ran this:** Attaches the ECR permissions to the role. This is an **inline policy** (embedded directly in the role).

**Inline vs Managed policy:**

| Type | Pros | Cons |
|------|------|------|
| Inline (what we used) | Simpler for single-role use, one less AWS resource to track | Can't reuse across multiple roles |
| Managed (customer-managed) | Reusable across roles, versioned, visible in IAM console | Slightly more setup |

**Output:** None on success. AWS CLI is **silent** when things work — this is normal and expected.

### `aws iam list-role-policies`

```bash
aws iam list-role-policies --role-name github-actions-onlineshop
```

**Why we ran this:** **Always verify.** After creating or modifying resources, confirm the change took effect.

**Expected output:** `{"PolicyNames": ["ecr-push-pull"]}` — confirmed.

---

## A4. ECR — "Where Docker Images Live"

### Background: What is ECR?

ECR (Elastic Container Registry) is AWS's private Docker image registry. It's where your container images are stored before ECS (Elastic Container Service) runs them.

The relationship: **ECR = storage, ECS = execution.** You push images to ECR, then ECS pulls them to run your services.

### `aws ecr describe-repositories` — checking multiple regions

```bash
aws ecr describe-repositories --region eu-north-1
aws ecr describe-repositories --region eu-central-1
aws ecr describe-repositories --region us-east-1
```

**Why we ran this against 3 regions:** ECR repositories are **region-scoped**. An ECR repo created in `eu-north-1` is invisible in `eu-central-1`. Since we didn't know which region was used in step 1.2, we checked all three likely candidates.

**What happened:** Repos found in `eu-north-1` only. Bingo — Stockholm is our region.

**Key fields:**

| Field | Meaning |
|-------|---------|
| `repositoryName` | The human-friendly name |
| `repositoryUri` | The full URL you use with `docker push` / `docker pull`: `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth` |
| `imageTagMutability` | `MUTABLE` = same tag can be overwritten. `IMMUTABLE` = each tag can only be pushed once (better for traceability) |
| `createdAt` | When the repo was created |

### Issue: Misnamed ECR repository

**What happened:** One repository was named `onlineshop-auth/api-gateway` instead of `onlineshop-api-gateway`. The slash `/` in ECR repository names is treated as a namespace separator in the AWS console, which is confusing and breaks conventions.

**Root cause:** Manual creation in step 1.2 likely had a typo or misunderstood the naming convention.

**How we detected it:** When listing ECR repos, we noticed the pattern didn't match:
```
onlineshop-auth            ← correct
onlineshop-items           ← correct
onlineshop-auth/api-gateway ← wrong! Should be onlineshop-api-gateway
```

### `aws ecr describe-images` — checking if a repo is empty

```bash
aws ecr describe-images --repository-name "onlineshop-auth/api-gateway" --region eu-north-1
```

**Why we ran this:** Before deleting the misnamed repo, we needed to check if it had images. Would be bad to accidentally delete actual image data.

**Output:** `"imageDetails": []` — empty. Safe to delete.

### `aws ecr create-repository`

```bash
aws ecr create-repository --repository-name "onlineshop-api-gateway" --region eu-north-1
```

**Why we ran this:** Create the correctly-named replacement repo.

**Convention:** Each deployable service gets its own ECR repository. This keeps:
- Images isolated (can't accidentally push auth code to the items repo)
- Lifecycle policies per-service (different retention rules per service)
- Clear ownership and traceability

### `aws ecr delete-repository`

```bash
aws ecr delete-repository --repository-name "onlineshop-auth/api-gateway" --region eu-north-1 --force
```

**Why we ran this:** Remove the misnamed repo. The `--force` flag is required because:
- Without `--force`, AWS refuses to delete a repo that has images (safety mechanism)
- With `--force`, it deletes the repo AND all images inside it
- Since our `describe-images` confirmed it was empty, `--force` is just a formality here

> **Warning:** `delete-repository --force` is destructive and irreversible. Always `describe-images` first.

### `aws ecr describe-images` — verifying pushed images

```bash
aws ecr describe-images --repository-name onlineshop-auth --region eu-north-1 --query "imageDetails[*].imageTags[0]"
```

**Why we ran this:** After the workflow succeeded, we confirmed images actually made it to ECR.

**Output:**
```
["sha-befc225cb8806ca139994013d02b6845a39b412b", "sha-263f0690aa08eaf24f23f715dea7e8895a759293"]
```

Each tag is the full `sha-<40-char-commit-hash>` — an immutable, traceable identifier.

---

# Part B: GitHub Actions Workflow — Full Issue Log

## B1. Commit-by-Commit History

We made **7 commits** to get the workflow working end-to-end. Each one fixed a specific issue. Here's the full debugging journey:

```
1765c89  chore:  remove temporary push trigger, keep workflow_dispatch only
263f069  fix:    remove maven-wrapper.jar from git tracking to prevent CRLF corruption
226bb34  fix:    renormalize api-gateway maven-wrapper.jar (failed fix attempt)
befc225  fix:    treat JARs as binary in git, add setup-buildx for Docker cache
4108ea8  fix:    use Java 25 to match project requirement (Spring Boot 4.0.x)
1dfb4a6  fix:    handle push event in job conditions to allow auto-triggering
c06d5f2  test:   add temporary push trigger for testing workflow on feature branch
ba27547  feat:   add GitHub Actions build-and-push workflow with OIDC to ECR (initial)
```

### Run #1 — Initial workflow [`ba27547`]

**What we expected:** Push code → GitHub registers the `workflow_dispatch` trigger → we can manually trigger it.

**What actually happened:** The workflow was invisible. `gh workflow list` showed only the two Claude workflows. The new workflow couldn't be found.

**Why:** GitHub Actions only **discovers** `workflow_dispatch` workflows when the workflow file exists on the **default branch** (`main`). Our file was on a feature branch (`build_and_release_first_iteration`), so GitHub's API and UI couldn't find it.

**Fix:** We temporarily added a `push` trigger so the workflow would auto-run on every push to our branch — no need for manual discovery. (Commit `c06d5f2`)

**Lesson:** `workflow_dispatch` workflows need to exist on `main` to be discoverable. During development on a feature branch, use `push` triggers for testing.

---

### Run #2 — Push trigger added, but all jobs skipped [`c06d5f2`]

**What we expected:** All three build jobs (auth, items, api-gateway) would run on push.

**What actually happened:** Status showed `completed / skipped` for the entire run.

**Why:** Each job had this condition:
```yaml
if: github.event.inputs.service == 'auth' || github.event.inputs.service == 'all'
```

`github.event.inputs` is **only populated for `workflow_dispatch` events**. On `push` events, `github.event.inputs` is `null`, so all three conditions evaluated to `false` and every job was skipped.

**Fix (commit `1dfb4a6`):** We added `github.event_name == 'push'` to every condition:
```yaml
if: github.event_name == 'push' || github.event.inputs.service == 'auth' || github.event.inputs.service == 'all'
```

Now on push events, the `github.event_name == 'push'` evaluates to `true` and the job runs. On dispatch events, the service selection logic takes over.

**Lesson:** `github.event.inputs` only exists for `workflow_dispatch` events. For multi-event workflows, always check `github.event_name` first.

---

### Run #3 — Maven build fails: Java version mismatch [`1dfb4a6`]

**What we expected:** Maven would compile the code and produce a JAR.

**What actually happened:** All three jobs failed at the Maven build step:
```
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.1:compile
Fatal error compiling: error: release version 25 not supported
```

**Why:** We configured `actions/setup-java@v4` with `java-version: '21'`, but the project's `pom.xml` specifies:
```xml
<java.version>25</java.version>
<maven.compiler.source>25</maven.compiler.source>
<maven.compiler.target>25</maven.compiler.target>
```

The Maven compiler plugin tried to compile for Java 25, but only Java 21 was installed. The error `release version 25 not supported` means "you asked me to compile for Java 25 but I'm running Java 21 and don't know how."

The Dockerfiles also confirmed this:
```dockerfile
FROM eclipse-temurin:25.0.1_8-jre-alpine   # ← Java 25!
```

**Fix (commit `4108ea8`):** Changed `java-version: '25'` in all three jobs.

**Lesson:** Always match `actions/setup-java` version to the `<java.version>` in `pom.xml`. Check Dockerfiles too — they're another source of truth for the Java version.

---

### Run #4 — Docker build fails: cache backend not supported [`4108ea8`]

**What we expected:** Maven would succeed (it did!) and Docker would build and push the image.

**What actually happened:** Maven passed for all three, but Docker build failed for auth and items:
```
ERROR: failed to build: Cache export is not supported for the docker driver.
```

**Why:** We configured Docker layer caching with:
```yaml
cache-from: type=gha
cache-to: type=gha,mode=max
```

These options require the **buildx** (BuildKit) Docker driver. The default `docker` driver on GitHub Actions runners doesn't support cache export. You must explicitly set up buildx first.

**Fix (commit `befc225`):** Added `docker/setup-buildx-action@v3` before each `docker/build-push-action@v6`:
```yaml
- uses: docker/setup-buildx-action@v3

- uses: docker/build-push-action@v6
  with:
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Lesson:** Whenever using `cache-from`/`cache-to` with `type=gha` in Docker build-push, you MUST run `setup-buildx-action` first. The default Docker driver on GitHub Actions doesn't support cache export.

---

### Run #5 — api-gateway Maven fails: corrupted JAR file [`befc225`]

**What we expected:** After the Docker cache fix, all three should pass.

**What actually happened:** Auth and items passed (Maven + Docker + push to ECR all green!), but api-gateway still failed at Maven:
```
Error: Could not find or load main class org.apache.maven.wrapper.MavenWrapperMain
```

**Why:** The Maven wrapper uses a JAR file (`.mvn/wrapper/maven-wrapper.jar`) to bootstrap Maven. This JAR was **corrupted** during git checkout because of a line-ending configuration issue.

The project's `.gitattributes` file had:
```
* text eol=lf
```

This means **every file** (including binary `.jar` files) is treated as **text** and subjected to line-ending conversion (CRLF → LF). When a binary file contains a byte sequence that happens to look like `\r\n` (0x0D 0x0A), git "helpfully" converts it to `\n` (0x0A), corrupting the binary content.

**Why only api-gateway?** The corruption is **probabilistic** — it depends on whether the binary JAR happens to contain the byte pattern `0x0D 0x0A` somewhere in its content. The api-gateway JAR happened to contain this pattern; the others didn't.

**Fix attempt 1 (commit `226bb34` — FAILED):** We tried `git add --renormalize` to re-process the JAR as binary after adding `*.jar binary` to `.gitattributes`. This didn't work because the JAR in git's object store was **already corrupted** — the `--renormalize` just confirmed the corrupt file matched the corrupt git object.

**Fix attempt 2 (commit `befc225`):** We added `*.jar binary` to `.gitattributes` to prevent future corruption. But this alone doesn't fix already-corrupted files.

**Final fix (commit `263f069`):** We removed all `maven-wrapper.jar` files from git tracking entirely and added `**/maven-wrapper.jar` to `.gitignore`. The mvnw script has auto-download logic — when the JAR is missing, it downloads a fresh copy from Maven Central. By not tracking the JAR in git, we:
1. Prevent any future CRLF corruption of JAR files
2. Reduce repo size (no binary JARs in version control)
3. Let the wrapper self-heal on every clean checkout

```bash
# Added to .gitignore
**/maven-wrapper.jar

# Remove from git tracking (but keep on disk)
git rm --cached **/maven-wrapper.jar
```

**Lesson:** Never put `* text` in `.gitattributes` without also adding `*.jar binary`, `*.png binary`, etc. for binary files. Better yet: let maven wrapper JARs be auto-downloaded.

---

### Run #6 — Final success! [`263f069`]

All three jobs passed:

| Job | Maven Build | Docker Build | Push to ECR | Duration |
|-----|------------|-------------|-------------|----------|
| build-auth | ✓ | ✓ | ✓ | 57s |
| build-items | ✓ | ✓ | ✓ | 44s |
| build-api-gateway | ✓ | ✓ | ✓ | 57s |

**Total workflow time:** ~1 minute (all jobs run in parallel)

---

### Run #7 — Cleanup [`1765c89`]

Removed the temporary `push` trigger. The final workflow uses `workflow_dispatch` only. When the workflow is merged to `main`, it will become discoverable via `gh workflow run` and the GitHub Actions UI.

---

## B2. Issue Deep-Dives

### Issue 1: Workflow Not Discoverable on Feature Branch

| What | Details |
|------|---------|
| **Symptom** | `gh workflow list` and `gh workflow run` couldn't find our new workflow |
| **Root cause** | GitHub only indexes `workflow_dispatch` workflows from the default branch (`main`) |
| **Why this happens** | The workflow index is built from `main` to prevent unreviewed workflows from being triggerable |
| **Our workaround** | Added a temporary `push` trigger for testing on the feature branch |
| **Proper fix** | Merge to `main` — then the workflow becomes discoverable everywhere |

### Issue 2: Job Conditions Skipping on Push Events

| What | Details |
|------|---------|
| **Symptom** | Workflow shows "skipped" for all jobs on push |
| **Root cause** | `github.event.inputs` is `null` on push events; only populated for `workflow_dispatch` |
| **Fix** | Added `github.event_name == 'push'` as an additional condition |

### Issue 3: Java 21 vs Java 25

| What | Details |
|------|---------|
| **Symptom** | `error: release version 25 not supported` during Maven compile |
| **Root cause** | We set `java-version: '21'` but `pom.xml` targets Java 25 (Spring Boot 4.0.x requires Java 25+) |
| **How we found it** | `gh run view --log` showed the Maven compiler error, then checked `pom.xml` |
| **Fix** | Changed `java-version: '25'` |

### Issue 4: Docker Cache Backend

| What | Details |
|------|---------|
| **Symptom** | `ERROR: failed to build: Cache export is not supported for the docker driver` |
| **Root cause** | `cache-to: type=gha` requires the buildx driver, but we didn't set it up |
| **Fix** | Added `docker/setup-buildx-action@v3` before each `build-push-action` |

### Issue 5: CRLF Corruption of Binary JARs

| What | Details |
|------|---------|
| **Symptom** | `Could not find or load main class org.apache.maven.wrapper.MavenWrapperMain` (api-gateway only) |
| **Root cause** | `.gitattributes` had `* text eol=lf` — treating ALL files including JARs as text, corrupting binary content during CRLF→LF conversion |
| **Why only api-gateway?** | Corruption is random — depends on whether the JAR bytes happen to contain a `0x0D 0x0A` sequence |
| **Failed fix** | `git add --renormalize` after adding `*.jar binary` — the JAR was already corrupted in git's object store |
| **Working fix** | Removed all `maven-wrapper.jar` from git tracking, added to `.gitignore`, let mvnw auto-download |
| **Prevention** | Added `*.jar binary` (and `*.png`, `*.jpg`, etc.) to `.gitattributes` |

---

# Part C: Quick Reference

### Key ARNs (Amazon Resource Names)

| Resource | ARN |
|----------|-----|
| IAM Role | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| OIDC Provider | `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com` |
| ECR — Auth | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth` |
| ECR — Items | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items` |
| ECR — API Gateway | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway` |

### Workflow File

- **Path:** `.github/workflows/build-and-push.yml`
- **Trigger:** `workflow_dispatch` (will be discoverable after merge to `main`)
- **Input:** `service` — choice of `auth`, `items`, `api-gateway`, or `all`

### Key Files Changed

| File | Why |
|------|-----|
| `.github/workflows/build-and-push.yml` | The build-and-push workflow |
| `.gitattributes` | Added `*.jar binary` to prevent future CRLF corruption |
| `.gitignore` | Added `**/maven-wrapper.jar` so mvnw auto-downloads JARs |
| `plans/.../01_MVP_DEPLOY.md` | Updated task status to track progress |

### Commands to Start a New Terminal Session

```bash
# Verify who you are
aws sts get-caller-identity

# Set the region (if not in aws config)
aws configure set region eu-north-1

# Check existing resources
aws ecr describe-repositories --region eu-north-1
aws iam list-open-id-connect-providers
aws iam list-role-policies --role-name github-actions-onlineshop

# Authenticate gh CLI (if not done)
gh auth status

# Trigger the workflow (after merged to main)
gh workflow run "Build &amp; Push to ECR" -f service=all
```

---

# Part D: ECS & RDS Operations (Staging Era — 2026-08-02)

> Everything here was learned the hard way during Pass 2 staging provisioning
# (~56% of that session's turns went to these issues). Read BEFORE touching
# ECS services, Service Connect, or the RDS instance.

## D1. Private RDS Access — The Only Sanctioned Pattern

| What | Details |
|------|---------|
| **Constraint** | `onlineshop-postgres-db` has `PubliclyAccessible: No` and lives in private subnets with no IGW route. It is unreachable from any developer machine, period |
| **Failed approaches** | Local `psql` via Docker (hangs → 120s shell timeout); deliberating public access / SSM / ECS Exec (all dead ends or overkill) |
| **Working pattern** | One-off Fargate task running `postgres:18-alpine` in the ECS security group (`sg-0b209104a6b15b157`), which RDS's SG already allows |
| **Codified in** | `scripts/ecs-run-sql.sh` — use it, don't hand-roll |

```bash
# Apply a schema and PROVE it landed — in the same run:
scripts/ecs-run-sql.sh --database auth_staging \
  --file Auth/init-db/01-schema.sql \
  --verify "SELECT tablename FROM pg_tables WHERE schemaname='public';"

# Run as a least-privilege service account (password from its own secret):
scripts/ecs-run-sql.sh --database items_staging \
  --user items_app_staging --secret onlineshop/items/db-staging \
  --command "SELECT count(*) FROM items;"
```

What the script encapsulates (each bullet was a multi-turn debugging episode):

1. **TD JSON built to a temp file** (`python3 json.dump`) and passed via
   `--cli-input-json file://` — inline `--container-definitions` with
   multi-line SQL fails with `Invalid control character`.
2. **SQL transported as base64** and decoded inside the container —
   zero shell/JSON quoting bugs regardless of SQL content.
3. **`psql -v ON_ERROR_STOP=1`** — psql exits non-zero on the first error
   instead of merrily continuing.
4. **`PGPASSWORD` injected from Secrets Manager** (`secrets[].valueFrom`),
   never a plaintext env var. Master creds live in `onlineshop/rds/master`;
   the execution role's `secretsmanager-read-onlineshop` policy covers
   `onlineshop/rds/*`.
5. **Log stream resolved correctly**: `<prefix>/<container>/<task-id>`,
   with retries for CloudWatch ingestion lag.
6. **Self-cleanup**: the TD revision is deregistered AND deleted
   (`delete-task-definitions`) after a successful run — deregister alone
   leaves it queryable as INACTIVE.

## D2. The "exit 0 but nothing happened" Trap

| What | Details |
|------|---------|
| **Symptom** | Schema apply task exits 0; app then crashes with `Schema validation: missing table [sessions]` |
| **Root cause** | A JSON-escaping bug in the hand-built container command meant the SQL was never actually applied (the `echo` of a JSON-encoded string wrote literal `\n` sequences). psql still exited 0 in an earlier combined run because of `&&`-chained commands and meta-command confusion |
| **Amplifier** | Log retrieval from the helper tasks failed (wrong stream-name guesses), so the failure was invisible for ~20 turns |
| **Rule** | **Exit 0 is not verification.** Every SQL mutation must be followed by a read-back query (`\dt`, `SELECT ...`) in the same run. The script's `--verify` flag exists for exactly this |

## D3. Service Connect Rules

| Rule | Violation error |
|------|-----------------|
| SC `portName` must match a `portMappings[].name` in the task definition | `portName(X) does not refer to any named PortMapping in the container definitions` |
| The Cloud Map service name derived from `portName` must be **unique per namespace** | `SC service is already used by arn:...:namespace/...` |

Consequence: staging TDs rename the *container port mapping names*
(`auth-port` → `auth-staging-port`) because prod already owns the plain
names in `onlineshop.local`. Changing only the SC `clientAliases.dnsName`
is NOT enough.

Check what names are taken:

```bash
aws servicediscovery list-services --profile dpm-profile --region eu-north-1 \
  --query 'Services[*].Name' --output table
```

## D4. `create-service` / `update-service` Flag Rules

| Rule | Error when violated |
|------|---------------------|
| `--launch-type` and `--capacity-provider-strategy` are mutually exclusive | `Specifying both a launch type and capacity provider strategy is not supported` |
| TD with container `secrets` requires `executionRoleArn` | `When you are specifying container secrets, you must also specify a value for 'executionRoleArn'` |
| `secrets[].valueFrom` with `:json-key::` suffix needs the FULL secret ARN (a bare name is parsed as an SSM parameter) | `The Systems Manager parameter name specified for secret X is invalid` |
| `update-service` has no `--launch-type` in the position you expect — check `aws ecs update-service help` before improvising flag combos | `Unknown options: --launch-type, FARGATE` |

## D5. Reading Logs from Crashed/Stopped Tasks

```bash
# Fastest path — no stream-name guessing:
aws logs filter-log-events --profile dpm-profile --region eu-north-1 \
  --log-group-name /ecs/onlineshop-auth \
  --start-time $(date -d '15 minutes ago' +%s)000 \
  --query 'events[*].message' --output text

# Stream names are deterministic:
#   <awslogs-stream-prefix>/<container-name>/<task-id>
# e.g. auth/auth/75396c8ee9d94c509897cd9a519e4c79
```

- `describe-tasks` does NOT reliably return `logStreamName` — construct it.
- CloudWatch ingestion lags a few seconds after task stop — retry before concluding "no logs".
- `startedAt: null` means "died during provisioning/early startup", not "image pull failed" — read the app logs before theorizing about IAM/secrets.
- Guard task ARN parsing: `list-tasks` returning empty → `None` → `describe-tasks` errors with `taskId length should be one of [32,36]`.

## D6. Deployment Behavior

| Phenomenon | Meaning | Action |
|-----------|---------|--------|
| Crash loop then `desired:1, running:0`, rollout `COMPLETED`, no new tasks | ECS stopped retrying the failing deployment | Fix root cause, then `update-service --force-new-deployment` |
| Health `UNKNOWN` for ~3 min on a fresh task | Container `healthCheck.startPeriod: 180` — checks haven't started | Normal. But if it persists past startPeriod+retries, check `list-tasks --desired-status STOPPED` for a crash loop instead of waiting |
| Service event "stopped 1 pending tasks" repeatedly | Tasks fail before reaching RUNNING | Same as above — logs first |

## D7. Secrets Hygiene in Task Definitions (Incident 2026-08-02)

During Pass 2, helper TD revisions were registered with the **master DB
password as a plaintext env var**, and staging passwords were echoed into
session logs. Remediation performed same day:

1. Master creds moved to Secrets Manager: `onlineshop/rds/master`
2. `ecsTaskExecutionRole` policy extended to `onlineshop/rds/*`
3. Both staging passwords rotated (`ALTER ROLE` + `put-secret-value`), verified by connecting as each service account
4. All 11 `onlineshop-psql-helper` revisions permanently removed via `delete-task-definitions` (deregister alone leaves them readable)

**Standing rules:**
- Passwords only ever enter tasks via `secrets[].valueFrom` — never `environment`, never embedded in `command`, never `echo`ed.
- One-off helper TD revisions: deregister AND delete after use.
- Need the new password inside SQL (rotations)? Put it in its secret FIRST, then reference it via `--extra-secret VAR=secret:password` and psql interpolation `:'VAR'` — never inline it.

## D8. Session-Efficiency Rules for Agents

- **No blocking poll loops** in a single shell call (a 48×10s loop hit the 600s hard timeout and lost everything). Use `aws ecs wait services-stable`, or loops bounded to <2 min and re-invoke.
- **Batch independent AWS reads** in one call (describe-services + list-tasks + logs in a single script) instead of one API call per turn.
- Fix the FIRST error class before scaling out — items-staging burned a 10-minute wait on the exact schema issue already diagnosed on auth-staging.
