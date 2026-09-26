# Auth Service Performance Tests

This directory contains k6 performance tests for the Auth service.

## Prerequisites

### Install k6

**Windows (Chocolatey):**
```bash
choco install k6
```

**Windows (Winget):**
```bash
winget install k6 --source winget
```

**macOS:**
```bash
brew install k6
```

**Linux:**
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```

**Docker:**
```bash
docker pull grafana/k6
```

## Quick Start

### 1. Start the Test Environment

```bash
# From the repository root
docker compose -f tests/performance/docker-compose.perf.yml up -d

# Wait for services to be healthy
docker compose -f tests/performance/docker-compose.perf.yml ps
```

### 2. Run Smoke Test (Quick Sanity Check)

```bash
cd tests/performance
k6 run smoke-1vu.js
```

### 3. Run Load Test (Realistic Traffic)

```bash
k6 run load.js
```

### 4. Run Stress Test (Find Breaking Points)

```bash
k6 run stress.js
```

### 5. Stop the Environment

```bash
docker compose -f tests/performance/docker-compose.perf.yml down -v
```

## Test Types

| Test | Purpose | Duration | VUs | When to Run |
|------|---------|----------|-----|-------------|
| **Smoke** | Quick sanity check | ~2 min | 1 | Every PR |
| **Load** | Realistic traffic | ~10 min | 20-50 | Nightly |
| **Stress** | Find limits | ~20 min | 50-300 | Weekly |

## Traffic Distribution

All tests use the following traffic mix (configurable):

- **80%** Token validation (`GET /api/v1/auth/validate`)
- **10%** Login (`POST /api/v1/auth/login`)
- **10%** Registration (`POST /api/v1/auth/register`)

Smoke test (`smoke-1vu.js`) enforces an exact per-iteration ratio of `1:1:8`:
- 1 register
- 1 login
- 8 validates split as:
- 7 validates with 7 distinct valid tokens (one per seeded smoke user)
- 1 validate with an invalid token (expected response `200` with `valid=false`)

## SLO Thresholds

| Endpoint | P95 Target | P99 Target |
|----------|------------|------------|
| Login | < 300ms | < 500ms |
| Validate | < 50ms | < 100ms |
| Register | < 500ms | < 800ms |

## Directory Structure

```
tests/performance/
├── README.md                    # This file
├── smoke-1vu.js                 # Smoke test (default 1 VU)
├── load.js                      # Load test
├── stress.js                    # Stress test
├── docker-compose.perf.yml      # Test environment
│
├── config/
│   ├── thresholds.js            # SLO definitions
│   ├── environments.js          # Environment URLs
│   └── test-users.json          # Test user data
│
├── utils/
│   ├── helpers.js               # Common functions
│   └── metrics.js               # Custom metrics
│
├── baselines/                   # Stored baseline results
│   └── .gitkeep
│
└── reports/                     # Generated reports
    └── .gitkeep
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENV` | `local` | Environment to test (`local`, `docker`) |

Example:
```bash
k6 run -e ENV=docker smoke-1vu.js
```

### Output Formats

**JSON output (for analysis):**
```bash
k6 run --out json=results.json load.js
```

**Console with summary:**
```bash
k6 run load.js
```

## Understanding Results

### Key Metrics

| Metric | Description |
|--------|-------------|
| `http_req_duration` | Total request time |
| `auth_login_duration` | Login endpoint timing |
| `auth_validate_duration` | Validate endpoint timing |
| `auth_register_duration` | Register endpoint timing |
| `errors` | Error rate (should be < 1%) |

### Percentile Meanings

| Percentile | Meaning |
|------------|---------|
| P50 | Median - typical experience |
| P95 | 95% of requests faster |
| P99 | 99% of requests faster |

### Sample Output

```
     ✓ login status is 200
     ✓ login has token
     ✓ validate status is 200

     checks.........................: 100.00% ✓ 1500  ✗ 0
     data_received..................: 245 kB  24 kB/s
     data_sent......................: 189 kB  19 kB/s

   ✓ auth_login_duration............: avg=45.23ms min=12ms med=38ms max=234ms p(90)=89ms p(95)=112ms
   ✓ auth_validate_duration.........: avg=8.45ms  min=2ms  med=6ms  max=45ms  p(90)=15ms p(95)=23ms

     http_req_duration..............: avg=32.12ms min=2ms  med=25ms max=234ms p(90)=67ms p(95)=89ms
   ✓ http_req_failed................: 0.00%   ✓ 0     ✗ 1500

     iterations.....................: 500     50/s
     vus............................: 50      min=0   max=50
     vus_max........................: 50      min=50  max=50
```

## Troubleshooting

### Service Not Responding

1. Check if the service is running:
   ```bash
   docker compose -f tests/performance/docker-compose.perf.yml ps
   ```

2. Check service logs:
   ```bash
   docker logs perf-auth-service
   ```

3. Verify health endpoint:
   ```bash
   curl http://localhost:9001/actuator/health
   ```

### High Error Rate

1. Check database connections:
   ```bash
   docker logs perf-auth-postgres
   ```

2. Check service memory:
   ```bash
   docker stats perf-auth-service
   ```

### Tests Running Slowly

1. Ensure Docker has sufficient resources
2. Check if other containers are consuming CPU/memory
3. Consider reducing VU count for local testing

## Advanced Usage

### Running with Docker

```bash
docker run --rm -i \
  --network=host \
  -v $(pwd):/tests \
  grafana/k6 run /tests/smoke-1vu.js
```

### Baseline Comparison

1. Run baseline test and save:
   ```bash
   k6 run --out json=baselines/baseline.json load.js
   ```

2. Run new test and compare:
   ```bash
   k6 run --out json=reports/current.json load.js
   # Compare manually or with a script
   ```

## Contributing

When adding new tests:

1. Follow the existing patterns in `smoke-1vu.js`, `load.js`, `stress.js`
2. Use the shared utilities from `utils/`
3. Add appropriate thresholds in `config/thresholds.js`
4. Update this README with any new test scenarios

