# Functional PRD — Bulk Action Platform for CRM

**Company:** Shipmnts
**Assignment:** Bulk Action Platform for CRM Application
**Version:** 1.0
**Initial Entity:** Contact
**Initial Action:** Bulk Update

---

## 1. Product Overview

The Bulk Action Platform is an asynchronous processing system that allows CRM users to perform operations on a large number of entities without keeping the API request open until processing completes.

For the initial implementation, the platform will support:

> **Bulk Update of Contact records**

The architecture must remain **entity-agnostic and action-agnostic**, allowing future support for entities such as:

* Contacts
* Companies
* Leads
* Opportunities
* Tasks

and actions such as:

* Update
* Delete
* Assign
* Tag

without requiring major changes to the core processing infrastructure.

---

# 2. Problem Statement

CRM administrators may need to modify thousands or potentially millions of records at once.

For example:

> An administrator wants to change all active contacts belonging to an account to `inactive`.

Processing these records synchronously through an API request is not suitable because:

* The operation may take a long time.
* HTTP requests should not remain open for large workloads.
* Large datasets should not be loaded into application memory.
* Processing needs to scale horizontally.
* Individual entity failures should not stop the entire operation.
* The system must provide progress and statistics.

Therefore, bulk actions will be processed **asynchronous, batch-based jobs**.

---

# 3. Goals

The system should:

1. Create bulk actions through an API.
2. Process large numbers of entities asynchronously.
3. Process entities in batches.
4. Support up to approximately **1 million entities architecturally**.
5. Demonstrate throughput of thousands of entities per minute.
6. Support horizontal worker scaling.
7. Track every entity's processing result.
8. Provide success, failure and skipped statistics.
9. Provide progress information.
10. Support retry of failed/incomplete work.
11. Prevent duplicate processing from causing incorrect results.
12. Make adding future bulk actions easy.
13. Keep the processing architecture entity-agnostic.
14. Provide APIs suitable for UI integration.
15. Provide Postman collection and architecture/demo documentation.

---

# 4. Non-Goals

For the initial implementation:

* No frontend/UI needs to be developed.
* Only one CRM entity needs to be implemented.
* Only Bulk Update needs to be implemented.
* No need to integrate with an external CRM.
* Production deployment is not required.
* Million-record testing is not required; the architecture should support it, while testing can use a few thousand entities.
* Scheduling is optional.
* Rate limiting is optional.
* Deduplication is optional.

---

# 5. Initial Domain Model

## 5.1 Contact

The initial CRM entity will be:

```text
Contact
```

Example fields:

```text
id
account_id
name
email
age
status
created_at
updated_at
```

Example:

```text
id:          101
account_id:  A001
name:        Rahul
email:       rahul@example.com
age:         28
status:      active
```

---

# 6. Bulk Action Concept

A **Bulk Action** represents one logical operation requested by a user.

Example:

> Change all active contacts belonging to account A001 to inactive.

Conceptually:

```json
{
  "accountId": "A001",
  "entity": "contact",
  "action": "update",
  "filter": {
    "status": "active"
  },
  "updates": {
    "status": "inactive"
  }
}
```

The request describes **what should happen**, rather than providing all matching entity IDs.

---

# 7. Record Selection Strategy

The system will use **filter-based selection** rather than requiring clients to provide potentially millions of entity IDs.

### Preferred

```json
{
  "filter": {
    "status": "active"
  }
}
```

### Not preferred for large workloads

```json
{
  "entityIds": [
    1,
    2,
    3,
    "... potentially millions ..."
  ]
}
```

### Reason

Filter-based requests:

* Keep API payloads small.
* Avoid forcing clients to retrieve entity IDs first.
* Avoid million-ID request payloads.
* Allow the backend to control batching.
* Scale better for large datasets.

---

# 8. API Responsibilities

The API layer is **not responsible for performing the bulk operation**.

Its responsibilities are:

```text
Receive request
      ↓
Validate request
      ↓
Create Bulk Action
      ↓
Enqueue execution
      ↓
Return actionId
```

The API must **not**:

```text
Fetch 100,000 records
Loop through them
Update them
Wait for completion
```

This keeps the API responsive regardless of workload size.

---

# 9. Bulk Action Lifecycle

The initial state machine will be:

```text
             ┌──────────┐
             │  QUEUED  │
             └────┬─────┘
                  │
                  ▼
             ┌──────────┐
             │ RUNNING  │
             └────┬─────┘
                  │
             ┌────┴─────┐
             ▼          ▼
       ┌──────────┐ ┌─────────┐
       │COMPLETED │ │  FAILED │
       └──────────┘ └─────────┘
```

Future states such as `CANCELLED` can be added later.

---

# 10. Proposed High-Level Architecture

```text
                     Client / Postman
                            │
                            ▼
                   ┌─────────────────┐
                   │     FastAPI     │
                   └────────┬────────┘
                            │
                            ▼
                    Create Bulk Action
                            │
                            ▼
                     Bulk Action DB
                            │
                            ▼
                    Execution Queue
                            │
                            ▼
                     ┌─────────────┐
                     │ Orchestrator│
                     └──────┬──────┘
                            │
                     Identify batches
                            │
                            ▼
                         Queue
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
          Worker 1       Worker 2       Worker 3
             │              │              │
             ▼              ▼              ▼
          Batch A        Batch B        Batch C
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                       Contact DB
                            │
                            ▼
                    Processing Logs
                            │
                            ▼
                        Statistics
```

The exact database and queue technology will be finalized during technical design.

---

# 11. Batch Processing

The system will process large workloads in manageable units.

For example:

```text
100,000 matching contacts

        ↓

Batch size = 1,000

        ↓

100 batch jobs
```

Workers process individual batches independently.

```text
Queue
 │
 ├── Batch 1 ──→ Worker 1
 ├── Batch 2 ──→ Worker 2
 ├── Batch 3 ──→ Worker 3
 ├── Batch 4 ──→ Worker 1
 ├── Batch 5 ──→ Worker 2
 ...
```

This provides dynamic workload distribution.

A worker should **not be permanently assigned a fixed group of batches**.

---

# 12. Chunk Size vs Batch Size

We identified two possible concepts:

### Chunk size

The amount of data retrieved from the database during one retrieval operation.

### Batch size

The amount of work represented by one processing job.

Example:

```text
100,000 records

chunk_size = 10,000
batch_size = 1,000
```

could conceptually produce:

```text
Chunk 1
 ├── Batch 1
 ├── Batch 2
 ├── ...
 └── Batch 10

Chunk 2
 ├── Batch 11
 ├── ...
```

However, for the first implementation we should prefer a **simpler batch-oriented retrieval model** unless benchmarking demonstrates that separate chunking is useful.

The key requirement is:

> Never load the entire million-record dataset into application memory.

---

# 13. Queue Message

We decided that the queue should **not contain the actual entity IDs**.

Instead, a queue message should reference the work.

Conceptually:

```json
{
  "actionId": "BA-12345",
  "batchId": "BATCH-42"
}
```

The worker can then retrieve:

```text
actionId
batchId
```

and determine:

```text
What action?
Which entity?
What filter/update?
Which records belong to this batch?
```

from the persistent source of truth.

### Reason

Avoid:

```text
Queue message
   ↓
[1000 entity IDs]
```

for every batch when the information can be represented through a batch reference.

This keeps queue messages small and allows the database to remain the authoritative source.

---

# 14. Worker Responsibility

A worker is responsible for processing **one batch of work**.

Conceptually:

```text
Receive batch
     ↓
Load bulk action
     ↓
Identify processor
     ↓
Retrieve batch records
     ↓
Process individual records
     ↓
Persist result
     ↓
Update statistics
     ↓
Acknowledge job
```

The worker should not need to know anything about HTTP/API request handling.

---

# 15. Processing Example

Suppose:

```text
Bulk Action = BA-123

Action:
UPDATE Contact

Filter:
status = active

Update:
status = inactive
```

A batch contains:

```text
1000 contacts
```

Worker processes:

```text
Contact 1   → SUCCESS
Contact 2   → SUCCESS
Contact 3   → SUCCESS
...
Contact 400 → SUCCESS
Contact 401 → ERROR
...
```

Each entity's outcome should be persisted.

---

# 16. Entity-Level Processing States

At the processing level, entities can have states such as:

```text
PENDING
PROCESSING
SUCCESS
FAILED
SKIPPED
```

Important distinction:

### Failed

The entity was actually processed and encountered an error.

### Pending

The entity has not yet been processed.

### Skipped

The system intentionally did not process the entity.

For example, deduplication could produce:

```text
email already exists → SKIPPED
```

Unprocessed records should **not** be incorrectly marked as failed merely because a worker crashed.

---

# 17. Failure and Retry

Suppose a worker receives:

```text
Batch 42
1000 records
```

and successfully processes:

```text
400 records
```

before crashing.

We want:

```text
1-400      → SUCCESS
401-1000   → PENDING / RETRYABLE
```

The queue should support acknowledgement semantics so that a job that was not successfully completed can become available for retry.

Another worker can subsequently process the batch.

---

# 18. Idempotency

Retries introduce a major problem:

```text
Worker 1:
1-400 processed successfully

Worker crashes

Worker 2:
receives same batch
```

Worker 2 must not incorrectly perform the operation multiple times.

Therefore, processing must be **idempotent or safely repeatable**.

The worker should be able to determine:

```text
Has this entity already successfully completed this bulk action?
```

If yes:

```text
Skip re-processing
```

This protects against duplicate work caused by worker crashes or queue retries.

---

# 19. Transactional Processing

Where appropriate, the business update and processing result should be persisted consistently.

Conceptually:

```text
BEGIN TRANSACTION

UPDATE contact

INSERT/UPDATE bulk_action_log
        ↓
      SUCCESS

COMMIT
```

This reduces the risk of:

```text
Contact updated
BUT
Success log missing
```

which could cause unnecessary reprocessing during retries.

The exact transaction boundaries will be finalized during implementation.

---

# 20. Bulk Action Statistics

The platform must expose:

```http
GET /bulk-actions/{actionId}/stats
```

Example response:

```json
{
  "actionId": "BA-12345",
  "total": 100000,
  "processed": 75000,
  "success": 72000,
  "failed": 2000,
  "skipped": 1000,
  "pending": 25000,
  "progress": 75.0
}
```

The statistics allow clients/UI to display progress without requiring the UI to understand internal processing.

---

# 21. Action Status API

```http
GET /bulk-actions/{actionId}
```

Example:

```json
{
  "actionId": "BA-12345",
  "entity": "contact",
  "action": "update",
  "status": "RUNNING",
  "total": 100000,
  "processed": 75000,
  "progress": 75.0,
  "createdAt": "...",
  "startedAt": "..."
}
```

---

# 22. Bulk Action List API

```http
GET /bulk-actions
```

Should allow clients to see actions such as:

```text
BA-101 → COMPLETED
BA-102 → RUNNING
BA-103 → QUEUED
BA-104 → FAILED
```

Filtering/pagination can be added as part of implementation.

---

# 23. Log Retrieval

Shipmnts requires the ability to retrieve and filter logs.

Therefore, the architecture should maintain persistent entity-level processing records.

Conceptually:

```text
bulk_action_logs

bulk_action_id
batch_id
entity_id
status
error_message
attempt_count
processed_at
```

Potential filtering:

```text
status=FAILED
status=SUCCESS
status=SKIPPED
```

The exact log endpoint is not explicitly mandated in the four core endpoints, so we can add one if useful:

```http
GET /bulk-actions/{actionId}/logs
```

---

# 24. Extensibility

The architecture should separate:

```text
Bulk Action Infrastructure
```

from:

```text
Business Action Processor
```

Conceptually:

```text
Bulk Action Engine
        │
        ▼
 Action Processor
        │
 ┌──────┼─────────┐
 ▼      ▼         ▼
Update Delete   Assign
```

Entity-specific processing should similarly be abstracted.

Initial implementation:

```text
Contact + Update
```

Future:

```text
Contact + Delete
Lead + Update
Company + Update
Contact + Assign
```

The API, queue, worker lifecycle, retry mechanism and statistics infrastructure should be reusable.

---

# 25. Horizontal Scaling

The system must support scaling workers horizontally.

Example:

```text
Low load:

Queue
 │
 Worker 1
```

Higher load:

```text
Queue
 ├── Worker 1
 ├── Worker 2
 ├── Worker 3
 └── Worker 4
```

Even higher load:

```text
Queue
 ├── Worker 1
 ├── Worker 2
 ├── ...
 └── Worker N
```

The producer should not need to know which worker processes a particular batch.

The queue is responsible for distributing available work.

---

# 26. Why We Need a Work Queue

Our processing requirement is primarily a **work queue**, rather than traditional broadcast Pub/Sub.

We want:

```text
One batch
      ↓
One worker processes it
```

rather than:

```text
One batch
      ↓
Worker 1
Worker 2
Worker 3
```

Broadcast-style Pub/Sub would be more appropriate if multiple independent services needed to consume the same event.

For bulk processing, we need competing consumers/workers.

---

# 27. API Contract

Core endpoints mandated by Shipmnts:

```http
GET  /bulk-actions
POST /bulk-actions
GET  /bulk-actions/{actionId}
GET  /bulk-actions/{actionId}/stats
```

Potential additional endpoint:

```http
GET /bulk-actions/{actionId}/logs
```

for the required log retrieval/filtering capability.

---

# 28. Example Complete User Journey

### Step 1 — Admin creates action

```http
POST /bulk-actions
```

```json
{
  "accountId": "A001",
  "entity": "contact",
  "action": "update",
  "filter": {
    "status": "active"
  },
  "updates": {
    "status": "inactive"
  }
}
```

### Step 2 — API creates action

```text
BA-123
status = QUEUED
```

### Step 3 — Execution is queued

```text
BA-123 → Queue
```

### Step 4 — Orchestrator creates batch work

```text
Batch 1
Batch 2
Batch 3
...
```

### Step 5 — Workers process batches

```text
Worker 1 → Batch 1
Worker 2 → Batch 2
Worker 3 → Batch 3
```

### Step 6 — Entity results are persisted

```text
SUCCESS
FAILED
SKIPPED
```

### Step 7 — Client checks progress

```http
GET /bulk-actions/BA-123
```

### Step 8 — Client checks statistics

```http
GET /bulk-actions/BA-123/stats
```

### Step 9 — Action completes

```text
RUNNING
   ↓
COMPLETED
```

---

# 29. Optional Enhancements

## Rate Limiting

Shipmnts suggests account-level limits such as:

```text
10,000 events/minute/account
```

We can introduce:

```text
accountId
```

on every bulk action.

This allows future implementation of:

```text
Account A → 10k/min
Account B → 10k/min
```

---

## Deduplication

Optional behavior:

```text
Duplicate email
      ↓
SKIPPED
      ↓
Log reason
```

Example:

```json
{
  "entityId": 123,
  "status": "SKIPPED",
  "reason": "Duplicate email"
}
```

This is a useful enhancement because it directly demonstrates the required `skipped` statistics.

---

## Scheduling

Future bulk actions could support:

```json
{
  "scheduledAt": "2026-11-22T23:15:00"
}
```

The system would delay execution until the scheduled time.

For the two-day assignment, this can initially be documented rather than implemented if time is constrained.

---

# 30. Performance Requirements

The system should be tested with at least a few thousand contacts and demonstrate throughput of:

> **Thousands of entities per minute**

Metrics to capture:

```text
Total entities
Total processing time
Entities/second
Entities/minute
Number of workers
Batch size
Success count
Failure count
Skipped count
```

Example:

```text
10,000 entities
5 workers
Batch size = 500

Processing time = X seconds
Throughput = Y entities/minute
```

The actual numbers will come from our load tests rather than being fabricated.

---

# 31. Reliability Requirements

The system should handle:

* Worker crashes
* Individual entity failures
* Batch retries
* Duplicate delivery
* Partial batch completion
* Database failures
* Queue failures where applicable

A failure of one entity should **not automatically terminate the entire bulk action**.

---

# 32. Key Architectural Principles

The implementation should follow these principles:

### 1. Asynchronous processing

API should return quickly.

### 2. Batch processing

Large workloads should be divided into manageable units.

### 3. Small queue messages

Use references such as:

```text
actionId
batchId
```

rather than unnecessarily large payloads.

### 4. Persistent state

Database is the source of truth for action and processing state.

### 5. Idempotency

Retries should not corrupt data or create unintended duplicate operations.

### 6. Horizontal scalability

Multiple workers should be able to consume batches concurrently.

### 7. Entity-agnostic architecture

Contact is only the first implementation.

### 8. Action-agnostic architecture

Bulk Update is only the first action.

### 9. Observability

Every entity's result should be traceable.

---

# 33. Decisions We Have Made So Far

| Decision                           | Direction                      |
| ---------------------------------- | ------------------------------ |
| Initial entity                     | Contact                        |
| Initial action                     | Bulk Update                    |
| Entity selection                   | Filter-based                   |
| API processing                     | Asynchronous                   |
| API loops through records?         | ❌ No                           |
| Large dataset in memory?           | ❌ No                           |
| Processing model                   | Queue + workers                |
| Work distribution                  | Dynamic                        |
| Queue message contains entity IDs? | ❌ No                           |
| Queue message                      | `actionId + batchId`           |
| Entity-level logs                  | ✅                              |
| Retry                              | ✅                              |
| Idempotency                        | ✅                              |
| Horizontal workers                 | ✅                              |
| Pub/Sub broadcast model            | ❌ Not primary processing model |
| UI                                 | ❌ Not required                 |
| Optional scheduling                | Document/implement if time     |
| Optional rate limiting             | Consider                       |
| Optional deduplication             | Strong candidate               |

---

## 34. Important Open Design Decisions

We **should not freeze these yet**:

### Database

Possible:

```text
PostgreSQL
```

### Queue

Possible:

```text
Redis
Redis Streams
RabbitMQ
Celery + Redis/RabbitMQ
```

### Batch retrieval strategy

Need to decide between:

```text
Keyset pagination
```

and mechanisms such as:

```text
SELECT ... FOR UPDATE SKIP LOCKED
```

depending on the final architecture.

### Batch claiming

Need to determine how multiple workers safely claim batches without processing the same batch concurrently.

### Statistics implementation

Need to decide whether statistics are:

```text
calculated from logs
```

or:

```text
maintained incrementally
```

or a hybrid.

---

# 35. Proposed Development Sequence

Now that our functional behavior is clear, I recommend we proceed in this exact order:

```text
PRD ✅
 │
 ▼
1. API Contract
 │
 ▼
2. Database Schema
 │
 ▼
3. Queue/Worker Architecture
 │
 ▼
4. Batch Creation + Claiming Strategy
 │
 ▼
5. Retry + Idempotency Design
 │
 ▼
6. Extensible Action Processor Design
 │
 ▼
7. FastAPI Implementation
 │
 ▼
8. Worker Implementation
 │
 ▼
9. Seed Data
 │
 ▼
10. Testing
 │
 ▼
11. Load Testing
 │
 ▼
12. Postman Collection
 │
 ▼
13. README + Architecture Diagram
 │
 ▼
14. Loom Demo
```

**This is now a solid functional PRD baseline.** The next thing I'd do is **design the database schema properly**—especially `bulk_actions`, `batches`, `bulk_action_logs`, and `contacts`—because our retry/idempotency and worker design will depend heavily on that schema.
