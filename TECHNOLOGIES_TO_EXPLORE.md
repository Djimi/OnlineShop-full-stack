# Technologies & Patterns to Explore

This document tracks technologies, architectural patterns, and testing approaches to learn and implement in this project.

## Status Legend
- [ ] Not started
- [~] In progress / Partially implemented
- [x] Completed

---

## Architectural Patterns

### CQRS (Command Query Responsibility Segregation)
- [ ] **Status:** Not started
- **Description:** Separate read and write models for different optimization strategies
- **Use Case:** High-read catalog with complex write operations
- **Implementation Ideas:**
  - Separate read/write repositories for Items
  - Denormalized read models optimized for queries
  - Event-based synchronization between models

### Event Sourcing
- [ ] **Status:** Not started
- **Description:** Store state changes as sequence of events, derive current state
- **Use Case:** Order history, audit trail, temporal queries
- **Implementation Ideas:**
  - Order aggregate with events (OrderPlaced, ItemAdded, PaymentReceived)
  - Event store (PostgreSQL or dedicated like EventStoreDB)
  - Projections for read models

### SAGA Pattern
- [ ] **Status:** Not started
- **Description:** Manage distributed transactions across services with compensating actions
- **Use Case:** Checkout flow (inventory → payment → order confirmation)
- **Implementation Ideas:**
  - Choreography-based (events) or Orchestration-based (saga coordinator)
  - Compensation handlers for rollback
  - State machine for saga lifecycle

### Transactional Outbox
- [ ] **Status:** Not started
- **Description:** Reliable event publishing using database transactions
- **Use Case:** Ensure events are published after successful database writes
- **Implementation Ideas:**
  - Outbox table in each service database
  - Polling or CDC (Change Data Capture) for publishing
  - Debezium for CDC with Kafka

---

## Technologies

### Messaging & Streaming

#### Apache Kafka
- [ ] **Status:** Not started
- **Description:** Distributed event streaming platform
- **Use Cases:**
  - Service-to-service async communication
  - Event sourcing event store
  - Real-time analytics
- **Learning Goals:**
  - Producers and consumers
  - Topics, partitions, consumer groups
  - Kafka Streams for processing
  - Schema Registry with Avro

### Real-Time Communication

#### WebSockets
- [ ] **Status:** Not started
- **Description:** Full-duplex communication over single TCP connection
- **Use Cases:**
  - Real-time inventory updates
  - Live order status tracking
  - Chat/notifications
- **Implementation Ideas:**
  - Spring WebSocket or WebFlux WebSocket
  - STOMP protocol over WebSocket
  - Frontend: native WebSocket or Socket.io

### Container Orchestration

#### Kubernetes
- [ ] **Status:** Not started
- **Description:** Container orchestration platform
- **Learning Goals:**
  - Pods, Deployments, Services
  - ConfigMaps and Secrets
  - Ingress controllers
  - Horizontal Pod Autoscaling
  - Helm charts
  - Local development: minikube, k3s, or kind

### Databases

#### MongoDB
- [ ] **Status:** Not started
- **Description:** Document database for flexible schemas
- **Use Cases:**
  - Product catalog with varying attributes
  - User preferences
  - Shopping cart

#### Elasticsearch
- [ ] **Status:** Not started
- **Description:** Search and analytics engine
- **Use Cases:**
  - Full-text product search
  - Faceted search (filters)
  - Analytics dashboards

### Warehouse Analytics (Local + Scalable)

**Ordering note (2026-01-31):** Items are sorted by current usage signals within their category: DB-Engines popularity for DBMS/engines, and GitHub stars for table formats/BI tools. Cross-category usage isn't directly comparable.

#### ClickHouse
- [ ] **Status:** Not started
- **Description:** Columnar OLAP database optimized for large-scale analytics
- **Use Cases:**
  - Warehouse analytics on events/orders
  - Fast aggregation for dashboards
  - Materialized views for rollups
- **Scaling Notes:** Scales from single-node laptop to multi-shard clusters; native replication/sharding.

#### DuckDB
- [ ] **Status:** Not started
- **Description:** Embedded analytical database for fast local analytics
- **Use Cases:**
  - Local data warehouse prototyping
  - SQL analytics over Parquet/CSV
  - Data exploration in notebooks/CLI
- **Scaling Notes:** Single-process but can scale up with larger machines; great for local-first workflows.

#### Trino
- [ ] **Status:** Not started
- **Description:** Distributed SQL query engine for federated analytics
- **Use Cases:**
  - Query data across multiple sources (object storage, Postgres, Kafka)
  - Warehouse-style analytics without moving data
  - BI integration via ANSI SQL
- **Scaling Notes:** Coordinator + workers model; scales horizontally from laptop to cluster.

#### Apache Druid
- [ ] **Status:** Not started
- **Description:** Real-time analytics datastore with OLAP + streaming ingest
- **Use Cases:**
  - Sub-second analytics dashboards
  - High-cardinality metrics
  - Event-driven warehouse slices
- **Scaling Notes:** Distributed architecture with segment replication; works locally with a single-node docker compose.

#### Apache Pinot
- [ ] **Status:** Not started
- **Description:** Real-time OLAP datastore with low-latency queries
- **Use Cases:**
  - Interactive analytics dashboards
  - Real-time aggregations on event streams
  - User-facing analytics features
- **Scaling Notes:** Distributed segments with replicas; supports single-node local runs.

#### Delta Lake
- [ ] **Status:** Not started
- **Description:** ACID table layer on top of Parquet for lakehouse workloads
- **Use Cases:**
  - Reliable ETL pipelines with ACID guarantees
  - Batch + streaming warehouse pipelines
  - Time travel auditing
- **Scaling Notes:** Scales with Spark compute; works locally via Spark + Delta.

#### Apache Iceberg
- [ ] **Status:** Not started
- **Description:** Table format for huge analytic datasets (data lakehouse)
- **Use Cases:**
  - Manage Parquet data with ACID semantics
  - Time travel and schema evolution
  - Interop with Trino/Spark/Flink
- **Scaling Notes:** Storage-agnostic; scales with object storage and compute engines.

#### Apache Hudi
- [ ] **Status:** Not started
- **Description:** Incremental data lake management for upserts/deletes
- **Use Cases:**
  - Change data capture into lakehouse tables
  - Incremental warehouse refresh
  - Streaming + batch ingestion
- **Scaling Notes:** Scales with Spark/Flink; can run locally in standalone mode.

#### Apache Superset
- [ ] **Status:** Not started
- **Description:** Open-source BI and visualization platform
- **Use Cases:**
  - Warehouse dashboards and KPI monitoring
  - Self-serve analytics for teams
  - Explore datasets from ClickHouse/Trino/Druid
- **Scaling Notes:** Stateless web app backed by metadata DB; scales with standard web deployments.

#### Metabase
- [ ] **Status:** Not started
- **Description:** Open-source analytics and dashboarding tool
- **Use Cases:**
  - Quick local dashboards for warehouse data
  - SQL + UI-based analytics exploration
  - Embedded analytics for product use
- **Scaling Notes:** Single-jar local run; scales via containerized deployment.

### Observability

#### Distributed Tracing (Micrometer + Zipkin/Jaeger)
- [ ] **Status:** Not started
- **Description:** Track requests across services
- **Learning Goals:**
  - Trace context propagation
  - Span creation and tagging
  - Visualization in Zipkin/Jaeger

#### Metrics (Prometheus + Grafana)
- [ ] **Status:** Not started
- **Description:** Metrics collection and visualization
- **Learning Goals:**
  - Custom metrics (counters, gauges, histograms)
  - Prometheus scraping
  - Grafana dashboards

#### Centralized Logging (ELK Stack)
- [ ] **Status:** Not started
- **Description:** Elasticsearch, Logstash, Kibana for log aggregation
- **Alternative:** Loki + Grafana (lighter weight)

---

## Testing Approaches

### Mutation Testing (PIT)
- [ ] **Status:** Not started
- **Description:** Introduce mutations to verify test quality
- **Tool:** PIT (pitest.org)
- **Learning Goals:**
  - Mutation operators (change conditions, remove statements)
  - Interpreting mutation score
  - Improving test effectiveness

### Property-Based Testing (jqwik)
- [ ] **Status:** Not started
- **Description:** Generate random test inputs within constraints
- **Tool:** jqwik for Java
- **Use Cases:**
  - Validation logic (email, passwords)
  - Mathematical operations (pricing)
  - Serialization/deserialization

### Performance Testing (Gatling/k6)
- [ ] **Status:** Not started
- **Description:** Load testing and performance benchmarking
- **Tools:** Gatling (Scala DSL) or k6 (JavaScript)
- **Learning Goals:**
  - Scenario modeling
  - Ramp-up patterns
  - Performance baselines
  - Bottleneck identification

### Chaos Engineering
- [ ] **Status:** Not started
- **Description:** Test system resilience by injecting failures
- **Tools:** Chaos Monkey, Litmus (Kubernetes)
- **Use Cases:**
  - Service failures
  - Network latency
  - Database unavailability
- **Prerequisite:** Kubernetes deployment

### Security Testing (OWASP)
- [ ] **Status:** Not started
- **Description:** Automated security vulnerability scanning
- **Tools:**
  - OWASP Dependency-Check (vulnerable dependencies)
  - SpotBugs + FindSecBugs (static analysis)
  - OWASP ZAP (dynamic scanning)

### Contract Testing
- [ ] **Status:** Not started
- **Description:** Verify API contracts between services
- **Tools:** Spring Cloud Contract or Pact
- **Learning Goals:**
  - Consumer-driven contracts
  - Provider verification
  - Contract versioning

---

## UI/Frontend

### State Management Alternatives
- [ ] **Status:** Zustand implemented
- **Explore:**
  - Redux Toolkit (larger apps)
  - Jotai (atomic state)
  - Recoil (Facebook's solution)

### Server-Side Rendering (Next.js)
- [ ] **Status:** Not started
- **Description:** React framework with SSR/SSG
- **Benefits:**
  - SEO optimization
  - Faster initial load
  - API routes

### Component Libraries
- [ ] **Status:** Custom components
- **Explore:**
  - shadcn/ui (copy-paste components)
  - Radix UI (headless primitives)
  - Headless UI (Tailwind Labs)

### Animation
- [ ] **Status:** Not started
- **Tools:**
  - Framer Motion
  - React Spring
  - CSS animations

---

## Priority Suggestions

### Phase 1: Core Patterns (Recommended First)
1. **Kafka** - Foundation for async communication
2. **CQRS** - Separate read/write for Items
3. **Transactional Outbox** - Reliable events with Kafka

### Phase 2: Resilience & Operations
1. **Kubernetes** - Container orchestration
2. **Distributed Tracing** - Observability
3. **Contract Testing** - API stability

### Phase 3: Advanced Patterns
1. **Event Sourcing** - For Order service
2. **SAGA** - Distributed transactions
3. **Chaos Engineering** - Resilience testing

### Phase 4: Enhanced Features
1. **WebSockets** - Real-time updates
2. **Elasticsearch** - Product search
3. **Performance Testing** - Load testing

---

## Notes

- Each technology should have an ADR when implemented
- Document lessons learned after exploring each item
- Focus on understanding concepts, not just implementation
- Consider trade-offs and when NOT to use each pattern
