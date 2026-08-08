# How We Deployed the Frontend to AWS: S3 + CloudFront Tutorial

> **Scope:** Everything done in Pass 1 Step 1.6 (frontend deployment) and the fixes applied during it. Written for someone who has never used S3, CloudFront, or CDN static hosting before.

---

## Table of Contents

1. [The Big Picture: Why S3 + CloudFront?](#1-the-big-picture-why-s3--cloudfront)
2. [What is Amazon S3?](#2-what-is-amazon-s3)
3. [What is Amazon CloudFront?](#3-what-is-amazon-cloudfront)
4. [Step-by-Step: Creating the S3 Bucket](#4-step-by-step-creating-the-s3-bucket)
5. [Step-by-Step: Building the Frontend](#5-step-by-step-building-the-frontend)
6. [Step-by-Step: Creating the CloudFront Distribution](#6-step-by-step-creating-the-cloudfront-distribution)
7. [CloudFront Cache Behaviors Explained](#7-cloudfront-cache-behaviors-explained)
8. [The `/items` vs `/items/*` Path Pattern Bug](#8-the-items-vs-items-path-pattern-bug)
9. [What is CORS and Why Did It Break?](#9-what-is-cors-and-why-did-it-break)
10. [Same-Origin vs Cross-Origin API Calls](#10-same-origin-vs-cross-origin-api-calls)
11. [The `||` vs `??` JavaScript Trap](#11-the--vs--javascript-trap)
12. [CloudFront Cache Invalidation](#12-cloudfront-cache-invalidation)
13. [How to Resume / Pause the Playground](#13-how-to-resume--pause-the-playground)
14. [Cost of What We Built](#14-cost-of-what-we-built)
15. [Quick Reference: Common Commands](#15-quick-reference-common-commands)

---

## 1. The Big Picture: Why S3 + CloudFront?

Our project has two parts:
- **Backend:** Three Java services (Auth, Items, API Gateway) running in Docker containers on AWS ECS
- **Frontend:** A React single-page application (SPA) built with Vite

Before this step, the backend was live on AWS behind an ALB (Application Load Balancer), but the frontend only ran on our laptops (`npm run dev`). The goal was to make the frontend accessible on the public internet too.

### Why not just run the frontend on ECS like the backend?

We *could*, but it would be wasteful. A React app is just static files (HTML, CSS, JS). It does not need a running server, a JVM, or CPU cycles waiting for requests. Hosting static files on S3 is:
- **Cheaper** — you pay only for storage (~$0.023/GB/month) and bandwidth
- **Faster** — CloudFront caches files at edge locations near users
- **Simpler** — no containers, no health checks, no JVM startup time

### Architecture after this step

```
User's Browser
      ↓ HTTPS
[d2akuwv5pxgajc.cloudfront.net]  ← CloudFront Distribution
      ├── /, /login, /items  →  S3 bucket (static React app)
      ├── /auth/*, /items/*  →  ALB (backend API Gateway)
```

---

## 2. What is Amazon S3?

**S3 (Simple Storage Service)** is AWS's object storage. Think of it as an infinite hard drive in the cloud where you store files as "objects" inside "buckets."

### Key concepts

| Term | Meaning | Analogy |
|---|---|---|
| **Bucket** | A top-level container for objects | A folder on your computer |
| **Object** | A file stored in a bucket | A file inside that folder |
| **Key** | The path/name of an object | `index.html`, `assets/logo.png` |
| **Region** | The AWS datacenter where the bucket lives | `eu-north-1` = Stockholm |

### S3 can act as a website

S3 has a feature called **Static Website Hosting**. When enabled, S3 serves files over HTTP directly. If a user requests `/`, S3 returns `index.html`. This is perfect for React SPAs.

### Important: S3 permissions

By default, S3 buckets are **private**. To serve a public website, we must:
1. Disable "Block Public Access" (a safety guardrail)
2. Attach a **Bucket Policy** that allows `s3:GetObject` from anyone (`"Principal": "*"`)

**Security note:** We only allow `GetObject` (read). No one can upload, delete, or list files without AWS credentials.

---

## 3. What is Amazon CloudFront?

**CloudFront** is AWS's Content Delivery Network (CDN). A CDN is a network of servers around the world that cache copies of your content. When a user in Tokyo requests your site, CloudFront serves it from a server in Tokyo instead of making them fetch it from Stockholm.

### Why use CloudFront instead of S3 directly?

| Feature | S3 Website | CloudFront |
|---|---|---|
| HTTPS | ❌ Only HTTP | ✅ HTTPS with free certificate |
| Custom domain | ❌ Long S3 URL | ✅ Can use your own domain |
| Edge caching | ❌ None | ✅ Global edge locations |
| Multiple origins | ❌ One S3 bucket | ✅ S3 + ALB + anything |
| Path-based routing | ❌ None | ✅ `/auth/*` → ALB, rest → S3 |

### Key CloudFront concepts

| Term | Meaning |
|---|---|
| **Distribution** | The CDN configuration. You create one distribution per website. |
| **Origin** | The source of the content. Can be S3, an ALB, an EC2 instance, or any HTTP server. |
| **Cache Behavior** | A rule that says "for paths matching X, use origin Y with these cache settings." |
| **Edge Location** | A small datacenter around the world that caches your content. |

---

## 4. Step-by-Step: Creating the S3 Bucket

### 4.1 Create the bucket

```bash
aws s3api create-bucket \
  --profile dpm-profile \
  --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --create-bucket-configuration LocationConstraint=eu-north-1
```

**What this does:**
- `s3api create-bucket` — Creates a new S3 bucket
- `--bucket` — The bucket name. Must be **globally unique** across all of AWS, which is why we include the AWS account ID.
- `--region eu-north-1` — Places the bucket in Stockholm
- `--create-bucket-configuration LocationConstraint=eu-north-1` — Required when creating a bucket in a region OTHER than `us-east-1`

**What happens:** AWS creates the bucket and returns:
```json
{
  "Location": "http://onlineshop-frontend-799111666795.s3.amazonaws.com/"
}
```

### 4.2 Disable Block Public Access

By default, AWS blocks ALL public access to new buckets to prevent accidental data leaks.

```bash
aws s3api delete-public-access-block \
  --profile dpm-profile \
  --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795
```

**What this does:** Removes the safety guardrail so we can attach a public-read policy.

### 4.3 Attach a public-read bucket policy

```bash
aws s3api put-bucket-policy \
  --profile dpm-profile \
  --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"
    }]
  }'
```

**Breaking down the policy:**
- `"Version": "2012-10-17"` — The IAM policy language version. Always use this.
- `"Effect": "Allow"` — This statement grants permission (rather than denying).
- `"Principal": "*"` — Applies to everyone (anonymous users included).
- `"Action": "s3:GetObject"` — Only allows reading files. No uploads, no deletes.
- `"Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"` — Applies to ALL objects inside this bucket. The `/*` at the end is critical.

### 4.4 Enable static website hosting

```bash
aws s3api put-bucket-website \
  --profile dpm-profile \
  --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --website-configuration '{
    "IndexDocument": {"Suffix": "index.html"},
    "ErrorDocument": {"Key": "index.html"}
  }'
```

**What this does:**
- `IndexDocument` — When a user visits `/`, S3 serves `index.html`
- `ErrorDocument` — When a file is not found (404), S3 ALSO serves `index.html`

**Why set ErrorDocument to index.html?**

In a React SPA, routing is handled by JavaScript in the browser. If a user directly visits `/login`, there is no `login.html` file on S3. Without the error document override, S3 would return a plain 404 XML error. By redirecting 404 → `index.html`, the React app loads and its router renders the login page.

### 4.5 Verify the bucket exists

```bash
aws s3api list-buckets \
  --profile dpm-profile \
  --region eu-north-1 \
  --query 'Buckets[*].Name'
```

---

## 5. Step-by-Step: Building the Frontend

### 5.1 How Vite handles environment variables

Vite uses a special prefix `VITE_` for environment variables that should be embedded into the client-side JavaScript bundle. Our frontend code reads:

```ts
// frontend/src/services/api.ts
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
```

**What `import.meta.env` is:**

In Vite (and modern ESM), `import.meta.env` is an object containing environment variables available at build time. Unlike Node.js `process.env`, this works in the browser because Vite replaces `import.meta.env.VITE_API_URL` with the actual string value during the build.

### 5.2 The `??` vs `||` trap

Originally, the code used `||`:
```ts
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:10000';
```

**Why this is wrong:**
- In JavaScript, `||` returns the right-hand side if the left-hand side is **falsy**.
- An empty string `''` is falsy in JavaScript.
- So when we set `VITE_API_URL=''`, the expression became `'' || 'http://localhost:10000'` → `'http://localhost:10000'`.

**The fix:** Use `??` (nullish coalescing):
```ts
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
```

`??` only falls back if the left side is `null` or `undefined`, NOT if it is an empty string. So `'' ?? 'http://localhost:10000'` correctly evaluates to `''`.

### 5.3 Why `VITE_API_URL=''`?

When `API_BASE_URL` is an empty string, Axios uses **relative URLs**. So a request to `/auth/login` becomes `https://d2akuwv5pxgajc.cloudfront.net/auth/login` in the browser. This is called a **same-origin request** because the frontend and the API share the same domain (`d2akuwv5pxgajc.cloudfront.net`).

**Why same-origin matters:** See the [CORS section below](#9-what-is-cors-and-why-did-it-break).

### 5.4 Build command

```bash
cd frontend
rm -rf dist
VITE_API_URL='' npm run build
```

**What this produces:**
- `dist/index.html` — The entry point
- `dist/assets/` — JavaScript and CSS bundles (hashed filenames for cache-busting)
- `dist/vite.svg` — Static assets

### 5.5 Upload to S3

```bash
aws s3 sync frontend/dist \
  s3://onlineshop-frontend-799111666795/ \
  --profile dpm-profile \
  --region eu-north-1 \
  --delete
```

**What `--delete` does:** Removes files from S3 that no longer exist in `dist/`. This is important because each build generates new hashed filenames (e.g., `index-DEfIbenB.js` becomes `index-DMb42Sm1.js`). Without `--delete`, old files accumulate forever.

---

## 6. Step-by-Step: Creating the CloudFront Distribution

### 6.1 Why we needed TWO origins

A CloudFront distribution can have multiple origins. We configured two:

1. **`s3-frontend`** — Serves the React app (static files)
2. **`alb-api`** — Forwards API calls to the backend ALB

This means users visit ONE URL (`https://d2akuwv5pxgajc.cloudfront.net`) and both the frontend and backend are available there.

### 6.2 The distribution configuration

CloudFront distributions are created via JSON configuration. Here is a simplified version of what we deployed:

```json
{
  "CallerReference": "onlineshop-frontend-2026-08-01",
  "Comment": "OnlineShop frontend + API gateway",
  "Origins": {
    "Quantity": 2,
    "Items": [
      {
        "Id": "s3-frontend",
        "DomainName": "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com",
        "CustomOriginConfig": {
          "HTTPPort": 80,
          "HTTPSPort": 443,
          "OriginProtocolPolicy": "http-only"
        }
      },
      {
        "Id": "alb-api",
        "DomainName": "onlineshop-alb-1163734147.eu-north-1.elb.amazonaws.com",
        "CustomOriginConfig": {
          "HTTPPort": 80,
          "HTTPSPort": 443,
          "OriginProtocolPolicy": "http-only"
        }
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3-frontend",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": { "Items": ["GET", "HEAD", "OPTIONS"] },
    "ForwardedValues": {
      "QueryString": false,
      "Cookies": { "Forward": "none" }
    },
    "MinTTL": 0,
    "DefaultTTL": 86400,
    "MaxTTL": 31536000
  }
}
```

**Key fields explained:**
- `CallerReference` — A unique ID you choose. If you try to create the same distribution twice, CloudFront rejects it based on this.
- `OriginProtocolPolicy: http-only` — CloudFront talks to S3/ALB over HTTP (not HTTPS). This is fine because the traffic stays inside AWS's private network.
- `ViewerProtocolPolicy: redirect-to-https` — Forces users to use HTTPS when accessing the CloudFront domain.
- `DefaultTTL: 86400` — Static files are cached for 1 day at edge locations.

### 6.3 Create command

```bash
aws cloudfront create-distribution \
  --profile dpm-profile \
  --region us-east-1 \
  --distribution-config file:///tmp/cloudfront-distribution.json
```

**Important:** CloudFront is a **global** service. You MUST use `--region us-east-1` for all CloudFront API calls, even though our infrastructure is in `eu-north-1`.

### 6.4 What happens after creation

CloudFront returns immediately with a `Status: InProgress`. Deployment to all edge locations worldwide takes **5–15 minutes**. During this time, the distribution is partially active — some edge locations may serve stale content or return errors.

**How to check status:**
```bash
aws cloudfront get-distribution \
  --profile dpm-profile \
  --region us-east-1 \
  --id EPS8MI3FV3B7X \
  --query 'Distribution.Status'
```

When it says `Deployed`, the distribution is live everywhere.

---

## 7. CloudFront Cache Behaviors Explained

### What is a cache behavior?

A cache behavior is a rule that tells CloudFront: "When the request path matches X, do Y."

### Our cache behaviors

| Path Pattern | Origin | Caching | Why |
|---|---|---|---|
| `Default (*)` | `s3-frontend` | 1 day | Static files change rarely |
| `/auth*` | `alb-api` | **No cache** | Auth responses must be fresh |
| `/items*` | `alb-api` | **No cache** | Items data changes; also requires auth header |

### Why API calls must NOT be cached

If CloudFront cached `/items`, it would serve the same JSON to every user, regardless of authentication. Worse, it might cache a 401 Unauthorized response and serve it to authenticated users!

**How we disable caching:**
```json
{
  "MinTTL": 0,
  "DefaultTTL": 0,
  "MaxTTL": 0
}
```

### Forwarded values for API calls

```json
{
  "QueryString": true,
  "Cookies": { "Forward": "all" },
  "Headers": {
    "Quantity": 2,
    "Items": ["Authorization", "Content-Type"]
  }
}
```

**Why this matters:**
- `QueryString: true` — Passes `?page=2` etc. to the backend
- `Cookies: all` — Preserves session cookies if we ever use them
- `Headers: Authorization, Content-Type` — **CRITICAL.** Without forwarding `Authorization`, the Bearer token would be stripped, and every API call would fail with 401.

---

## 8. The `/items` vs `/items/*` Path Pattern Bug

### The bug

Our first cache behavior used `/items/*` as the path pattern. In CloudFront, `*` is a wildcard that matches **any sequence of characters**.

- `/items/123` → matches `/items/*` ✅
- `/items` → does NOT match `/items/*` ❌

When a user visited `https://...cloudfront.net/items` (exact path, no trailing slash), CloudFront fell through to the **default behavior** (S3 origin). S3 had no `items.html` file, so it returned the `index.html` error document. The browser received HTML instead of JSON, and the API call failed silently.

### The fix

We changed the path pattern from `/items/*` to `/items*`. Now it matches:
- `/items` → matches `/items*` ✅
- `/items/123` → matches `/items*` ✅
- `/items?page=2` → matches `/items*` ✅

### How we updated it

CloudFront distributions are immutable in the sense that you cannot edit them in-place. You must:
1. Get the current configuration (`get-distribution-config`)
2. Modify the JSON
3. Submit the new configuration (`update-distribution`) with an `If-Match` header containing the current ETag

```bash
# Get current config + ETag
aws cloudfront get-distribution-config \
  --id EPS8MI3FV3B7X \
  --query '{ETag: ETag, Config: DistributionConfig}'

# Submit updated config
aws cloudfront update-distribution \
  --id EPS8MI3FV3B7X \
  --distribution-config file:///tmp/updated-config.json \
  --if-match ETVPDKIKX0DER
```

After updating, CloudFront goes back to `InProgress` for another 5–15 minutes.

---

## 9. What is CORS and Why Did It Break?

### What is CORS?

**CORS (Cross-Origin Resource Sharing)** is a browser security feature. It prevents websites from making requests to a different domain unless the target server explicitly allows it.

**Example:**
- Your frontend is at `https://d2akuwv5pxgajc.cloudfront.net`
- Your API is at `http://onlineshop-alb-...elb.amazonaws.com`
- These are **different origins** (different domains + different protocols)
- The browser blocks the request unless the API says "I allow this origin"

### Why our setup was especially tricky

Because we use CloudFront with a **single domain**, the frontend and API share the same origin:
- Frontend loads from `https://d2akuwv5pxgajc.cloudfront.net/`
- API calls go to `https://d2akuwv5pxgajc.cloudfront.net/auth/login`

This is **same-origin**, so CORS should not apply... except during testing and if anyone accesses the ALB directly.

### What actually broke

The FIRST frontend build still had `API_BASE_URL = 'http://localhost:10000'` hardcoded (because of the `||` bug). When we tested in the browser, the frontend tried to call `http://localhost:10000/auth/login` from `https://d2akuwv5pxgajc.cloudfront.net`. This is **cross-origin** (HTTPS → HTTP, different host), and the browser blocked it with:

```
net::ERR_CONNECTION_REFUSED
```

After fixing the `||` bug and rebuilding, the frontend used relative URLs. But we still saw:

```
403 Invalid CORS request
```

### Why? The Spring Boot CORS configuration

Our API Gateway (Spring Boot) had a CORS configuration that only allowed `localhost` origins:

```java
// BEFORE (broken for production)
registry.addMapping("/**")
    .allowedOrigins(
        "http://localhost:5173",
        "http://localhost:3000",
        ...
    )
    .allowCredentials(true);
```

Even though our CloudFront setup was same-origin, Spring Boot's CORS filter intercepted the request and rejected it because the `Origin` header did not match the whitelist.

### The fix

We changed the CORS configuration to allow all origins and disable credentials:

```java
// AFTER (working)
registry.addMapping("/**")
    .allowedOrigins("*")
    .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD")
    .allowedHeaders("*")
    .allowCredentials(false)
    .maxAge(3600);
```

```yaml
# application.yml
spring:
  cloud:
    gateway:
      mvc:
        cors:
          cors-configurations:
            '[/**]':
              allowed-origins: ["*"]
              allow-credentials: false
```

**Why `allowCredentials: false`?**

Browsers enforce a rule: if `Access-Control-Allow-Origin` is `*`, you cannot also send cookies (`allowCredentials: true`). Since our app uses Bearer tokens in the `Authorization` header (not cookies), we do not need credentials.

### How CORS actually works (the full flow)

For a `POST /auth/login` request from the browser:

1. **Browser sends a preflight `OPTIONS` request:**
   ```
   OPTIONS /auth/login
   Origin: https://d2akuwv5pxgajc.cloudfront.net
   Access-Control-Request-Method: POST
   Access-Control-Request-Headers: Content-Type
   ```

2. **Server responds with CORS headers:**
   ```
   HTTP/1.1 200 OK
   Access-Control-Allow-Origin: *
   Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD
   Access-Control-Allow-Headers: *
   ```

3. **Browser checks:** Is my origin allowed? Is my method allowed? Yes → proceed.

4. **Browser sends the actual `POST` request.**

**Without the CORS headers, the browser stops at step 3 and never sends the actual request.** The error appears in the browser console, but the network tab shows only the OPTIONS request.

---

## 10. Same-Origin vs Cross-Origin API Calls

### Definitions

Two URLs are the **same origin** if they share:
- Protocol (`https://` vs `http://`)
- Host (`example.com`)
- Port (`:443` vs `:10000`)

**Same-origin example:**
```
https://d2akuwv5pxgajc.cloudfront.net/          ← frontend
https://d2akuwv5pxgajc.cloudfront.net/items   ← API call
```
✅ Same origin — no CORS needed

**Cross-origin example:**
```
https://d2akuwv5pxgajc.cloudfront.net/                    ← frontend
http://onlineshop-alb-1163734147.eu-north-1.elb.amazonaws.com/items  ← API
```
❌ Cross-origin — CORS required

### Why same-origin is better for our architecture

| Aspect | Same-Origin (CloudFront) | Cross-Origin (direct ALB) |
|---|---|---|
| CORS | Not needed | Must configure |
| HTTPS | CloudFront provides it | ALB must be configured for HTTPS |
| Token security | Sent over HTTPS | Sent over HTTP (unless ALB has HTTPS) |
| URL simplicity | One domain | Two domains to manage |

---

## 11. The `||` vs `??` JavaScript Trap

### The full story

Our frontend build environment variable was set like this:
```bash
VITE_API_URL='' npm run build
```

In the code:
```ts
// BROKEN:
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:10000';
// When VITE_API_URL = ''
// → '' || 'http://localhost:10000'
// → 'http://localhost:10000'  (because '' is falsy)

// FIXED:
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
// When VITE_API_URL = ''
// → '' ?? 'http://localhost:10000'
// → ''  (because '' is NOT null/undefined)
```

### Falsy values in JavaScript

These values are **falsy** (evaluate to `false` in boolean context):
- `false`
- `0`
- `''` (empty string)
- `null`
- `undefined`
- `NaN`

`||` checks for **falsy** values. `??` checks only for **`null` or `undefined`**.

**Rule of thumb:** Use `??` for default values when `0` or `''` are valid inputs. Use `||` when you want to reject ALL falsy values.

---

## 12. CloudFront Cache Invalidation

### What is invalidation?

When you upload new files to S3, CloudFront edge locations may still serve the **old cached version** for up to the TTL (time-to-live). Invalidation tells CloudFront: "Delete your cached copy and fetch fresh content from the origin."

### How to invalidate

```bash
aws cloudfront create-invalidation \
  --profile dpm-profile \
  --region us-east-1 \
  --distribution-id EPS8MI3FV3B7X \
  --paths "/*"
```

**What `"/*"` means:** Invalidate ALL cached files. You can also target specific paths like `/index.html` or `/assets/*`.

**Cost note:** AWS gives you **1,000 free invalidations per month**. After that, they charge per invalidation path. For a small project, invalidating `/*` on every deploy is fine.

**Timing:** Invalidations typically complete within **1–2 minutes**, much faster than distribution updates (which take 5–15 minutes).

---

## 13. How to Resume / Pause the Playground

### Why pause?

The ALB costs **~$24/month** even with zero traffic. ECS tasks cost **~$0.02–0.05/hour each**. When paused, we keep only the cheap resources (S3, CloudFront, RDS Free Tier, Secrets Manager).

### Resume (start backend)

```bash
bash scripts/resume-playground.sh
```

This script:
1. Creates the ALB, target group, and listener
2. Scales all 3 ECS services to 1 task
3. Waits for services to be healthy (up to 5 minutes)

### Pause (stop backend)

```bash
bash scripts/pause-playground.sh
```

This script:
1. Scales all ECS services to 0
2. Deletes the ALB listener, target group, and ALB

**Important:** CloudFront and S3 remain active. The frontend URL (`https://d2akuwv5pxgajc.cloudfront.net`) still loads the React app, but API calls will fail because the backend is down.

---

## 14. Cost of What We Built

### New resources added in this step

| Resource | Monthly Cost |
|---|---|
| S3 storage (~400 KB) | ~$0.00 |
| S3 data transfer (minimal) | ~$0.00 |
| CloudFront data transfer (minimal) | ~$0.00 |
| CloudFront HTTPS requests (minimal) | ~$0.00 |
| **Total new cost** | **~$0.00–0.10** |

### Full stack cost summary

| State | Monthly Cost | What's Running |
|---|---|---|
| **Paused** | ~$1.25 | RDS, Secrets, ECR, Cloud Map, S3, CloudFront |
| **Running (Spot)** | ~$49.00 | Everything above + 3 ECS tasks + ALB |
| **Running (Spot 8h/day + ALB paused)** | ~$17–18 | ECS runs 8h/day, ALB deleted when not needed |

---

## 15. Quick Reference: Common Commands

### S3
```bash
# Create bucket
aws s3api create-bucket --bucket onlineshop-frontend-799111666795 --region eu-north-1 --create-bucket-configuration LocationConstraint=eu-north-1 --profile dpm-profile

# Make public
aws s3api delete-public-access-block --bucket onlineshop-frontend-799111666795 --profile dpm-profile --region eu-north-1
aws s3api put-bucket-policy --bucket onlineshop-frontend-799111666795 --policy '{"Version":"2012-10-17","Statement":[{"Sid":"PublicReadGetObject","Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::onlineshop-frontend-799111666795/*"}]}' --profile dpm-profile --region eu-north-1

# Enable website
aws s3api put-bucket-website --bucket onlineshop-frontend-799111666795 --website-configuration '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}' --profile dpm-profile --region eu-north-1

# Upload
aws s3 sync frontend/dist s3://onlineshop-frontend-799111666795/ --delete --profile dpm-profile --region eu-north-1
```

### CloudFront
```bash
# Check status
aws cloudfront get-distribution --id EPS8MI3FV3B7X --query 'Distribution.Status' --profile dpm-profile --region us-east-1

# Invalidate cache
aws cloudfront create-invalidation --distribution-id EPS8MI3FV3B7X --paths "/*" --profile dpm-profile --region us-east-1
```

### Frontend Build
```bash
cd frontend
rm -rf dist
VITE_API_URL='' npm run build
```

### Playground Control
```bash
# Start backend
bash scripts/resume-playground.sh

# Stop backend (save money)
bash scripts/pause-playground.sh
```

### Smoke Test
```bash
CF="https://d2akuwv5pxgajc.cloudfront.net"

# Frontend
curl -s -o /dev/null -w "%{http_code}" "$CF/"

# Register
curl -s -X POST "$CF/auth/register" -H "Content-Type: application/json" -d '{"username":"demo","password":"demo123"}'

# Login
TOKEN=$(curl -s -X POST "$CF/auth/login" -H "Content-Type: application/json" -d '{"username":"demo","password":"demo123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Items
curl -s "$CF/items" -H "Authorization: Bearer $TOKEN"
```

---

## Glossary

| Term | Definition |
|---|---|
| **ALB** | Application Load Balancer — distributes HTTP traffic to ECS tasks |
| **CDN** | Content Delivery Network — caches content at edge locations worldwide |
| **CORS** | Cross-Origin Resource Sharing — browser security feature for cross-domain requests |
| **ECS** | Elastic Container Service — runs Docker containers on AWS |
| **Edge Location** | A small AWS datacenter that caches CloudFront content |
| **Origin** | The source server that CloudFront fetches content from |
| **Preflight** | An `OPTIONS` request the browser sends before a cross-origin request |
| **S3** | Simple Storage Service — AWS object storage |
| **SPA** | Single Page Application — a web app that loads one HTML page and updates it dynamically |
| **TTL** | Time To Live — how long CloudFront caches content before checking the origin |
| **Wildcard** | A pattern like `*` that matches any characters in a path |
