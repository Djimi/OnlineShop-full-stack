# Git Workflow

## Branch Naming

- Features: `feature/<description>` (e.g., `feature/add-cart-service`)
- Bug fixes: `bug/<description>` (e.g., `bug/fix-auth-token-refresh`)
- **Never commit directly to main**

## Commit Message Format (Conventional Commits)

```
<type>(<scope>): <description>

[optional body]

Types: feat, fix, refactor, test, docs, chore, perf
Scopes: auth, items, gateway, frontend, e2e, docs
```

### Examples

```
feat(auth): add password reset endpoint
fix(items): correct price calculation for bulk discounts
test(e2e): add checkout flow scenarios
```

## Guarded PR merges

Resolve the reviewed head from Git or the GitHub API immediately before merging;
never reconstruct a full SHA from memory or an abbreviated log. Require the
reviewed head with `gh pr merge <pr> --match-head-commit <verified-sha>` after its
CI passes. A mismatch must stop the merge, not cause the guard to be removed.

## Never Track Auto-Downloadable Binaries

Build artifacts and tools that can be downloaded automatically should NOT be in git:

| File/Dir | Reason | In .gitignore? |
|-----------|--------|----------------|
| `**/maven-wrapper.jar` | Auto-downloaded by `mvnw` | Yes |
| `node_modules/` | Auto-installed by `npm install` | Yes (in frontend/) |
| `target/` | Maven build output | Yes (in each service) |
| `dist/` | Frontend build output | Yes (in frontend) |
| `*.class` | Java compiled classes | Yes |

Keeping these out of git prevents CRLF corruption of binary files, reduces repository size, and lets the tool auto-repair on any fresh checkout.
