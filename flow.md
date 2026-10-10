# Current Data Flow

This diagram reflects the implementation in this project. The durable queue is stored in the database; Redis is not used. The action-selection/orchestration role is currently part of each worker process rather than a separate service.

```mermaid
flowchart TD
    Client[Client / Dashboard / Postman]
    API[FastAPI]
    ActionTable[(bulk_actions<br/>action queue and request state)]
    BatchTable[(bulk_action_batches<br/>durable batch queue)]
    Contacts[(contacts<br/>source and updated records)]
    Logs[(bulk_action_logs<br/>per-entity outcomes)]
    Stats[Status / statistics / batch / log APIs]
    UI[Dashboard<br/>polls live state]

    Client -->|POST /bulk-actions: filters + updates| API
    API -->|Validate and insert QUEUED action| ActionTable
    API -->|202 Accepted: actionId| Client

    subgraph Workers[One or more worker processes]
        Loop[Worker polling loop: run_once]
        ClaimAction[Claim an incomplete action<br/>PostgreSQL: FOR UPDATE SKIP LOCKED]
        Count[Count matching source records<br/>and record selection target]
        Select[Keyset-select matching IDs<br/>account + filters + cursor<br/>limited by selection page size]
        Split[Split selected IDs<br/>into configured batch sizes]
        ClaimBatch[Claim one available batch<br/>PostgreSQL: FOR UPDATE SKIP LOCKED]
        Process[Load pending logs and contacts<br/>recheck account and filters<br/>apply contact update]
        Result{Record outcome}
        CompleteBatch[Mark batch complete<br/>and clear lease within transaction]
        Retry[Infrastructure error handler<br/>backoff and retry limit]
        Finalize[Finalize action after selection ends<br/>and no queued/running batches remain]
    end

    ActionTable -->|claim action and set selection lease| ClaimAction
    Loop --> ClaimAction
    ClaimAction --> Count
    Count --> Contacts
    Count --> Select
    Select -->|Read one bounded page| Contacts
    Select --> Split
    Split -->|Insert batch references| BatchTable
    Split -->|Insert entity IDs as PENDING result rows| Logs
    Split -->|Advance cursor and staged total| ActionTable
    BatchTable --> ClaimBatch
    Loop --> ClaimBatch
    ClaimBatch -->|Set RUNNING, lease, attempt and worker ID| BatchTable
    ClaimBatch --> Process
    Process -->|Lock and read PENDING rows| Logs
    Process -->|Lock selected contacts| Contacts
    Process --> Result
    Result -->|Eligible and update succeeds| Success[Set log SUCCESS]
    Result -->|No longer matches filter| Skipped[Set log SKIPPED]
    Result -->|Record-level update error| Failed[Set log FAILED]
    Success --> CompleteBatch
    Skipped --> CompleteBatch
    Failed --> CompleteBatch
    CompleteBatch --> Commit[Commit one batch transaction<br/>contact changes + entity logs + batch state]
    Commit --> Contacts
    Commit --> Logs
    Commit --> BatchTable
    Process -.->|Unexpected infrastructure error| Retry
    Retry -->|Retryable: QUEUED with available_at backoff| BatchTable
    Retry -->|Attempts exhausted: FAILED| BatchTable
    ActionTable --> Finalize
    BatchTable --> Finalize
    Finalize -->|COMPLETED or FAILED| ActionTable

    ActionTable --> Stats
    BatchTable --> Stats
    Logs --> Stats
    Contacts --> Stats
    Stats -->|JSON status, progress, outcomes and worker assignments| Client
    Stats --> UI
    UI -->|Refreshes about once per second| Stats

    classDef queue fill:#e7f2f4,stroke:#087e8b,color:#17313b;
    classDef worker fill:#fff4dc,stroke:#d99a20,color:#493510;
    classDef data fill:#edf5ee,stroke:#3a956d,color:#173b2b;
    class ActionTable,BatchTable queue;
    class Loop,ClaimAction,Count,Select,Split,ClaimBatch,Process,Result,CompleteBatch,Retry,Finalize worker;
    class Contacts,Logs,Stats data;
```

## Processing notes

- PostgreSQL is the supported database for multiple competing workers. Each worker claims a different available action or batch row with `SKIP LOCKED`; no worker is permanently assigned a batch.
- PostgreSQL is required for both local and scaled runs. SQLite is not supported because it cannot provide the row-lock coordination used by competing workers.
- The queue contains durable action and batch references. Entity IDs are staged in `bulk_action_logs` and are not sent as a large queue payload.
- Selection is keyset-paged and bounded by `SELECTION_PAGE_SIZE`; each page is split into batches no larger than `BATCH_SIZE`.
- A batch’s record updates and result logs are committed in one transaction. If that transaction rolls back, the batch remains retryable and its uncommitted record outcomes are retried together.
- In-progress record counts are inferred from pending logs attached to running batches. Individual log rows do not currently transition to a persisted `PROCESSING` status.
- Record-level `FAILED` outcomes do not fail the whole action when the batch completes. The action becomes `FAILED` when infrastructure retries exhaust a batch; otherwise it completes after all selection and queued work finish.
- A separate orchestrator service is not implemented. Every worker loop can claim an action page and can claim a batch; the orchestration role is logical, not a separate process.
