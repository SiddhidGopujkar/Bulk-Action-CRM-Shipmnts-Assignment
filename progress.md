# Project Progress — Bulk Action Platform

**Project:** Shipmnts Bulk Action Platform for CRM
**Updated:** 2026-10-10

This file records the implementation and validation completed so far, along with the outstanding work. Performance figures below are measurements from the stated setup; they are not predictions for other databases or machines.

## Implemented

### Asynchronous bulk-action backend

- FastAPI endpoints accept contact bulk-update requests, validate fields, persist the action, and return `202 Accepted` with an action ID. The API does not perform the bulk update in the request handler.
- The initial processor supports `contact/update`, account scoping, equality filters on `status`, `name`, `email`, and `age`, and updates to those same fields.
- Workers independently poll persisted action and batch work. They keyset-page through matching contact IDs, split each page into configurable batches, and persist per-entity result rows. Selection and batch sizes are configurable.
- Each batch records its status, attempts, lease, assigned worker ID, and error. PostgreSQL workers claim work with `FOR UPDATE SKIP LOCKED`, allowing competing workers to take separate rows. PostgreSQL is now the only supported runtime database; SQLite support has been removed.
- Contact changes and their entity results are committed in a database transaction. A record-level error is recorded without stopping the rest of that batch. Infrastructure failures are retried with backoff and a configurable attempt limit.
- A retry endpoint requeues failed batches and failed entity logs while retaining successful entity results.
- A PostgreSQL-locked fixed UTC-minute window caps processing at 10,000 entity events per account across all workers. Rate-limited batches are deferred without using retry attempts, and a read API exposes current account usage.
- A standalone `app.simulate_worker_failure` helper claims a staged batch, shortens the lease for a demo, and exits before processing so healthy workers can demonstrate lease recovery.
- Rate-limit verification reserved 10,000 events across PostgreSQL transactions, deferred event 10,001, and confirmed the API usage route. A synthetic claimed batch was requeued for the next UTC minute without consuming a worker retry attempt.
- Existing databases receive additive startup migrations for the worker ID and action selection/progress fields.
- The shared execution engine now uses the processor contract for entity selection, keyset paging, locked loading, account-scope checks, eligibility, validation, and applying operation data. Processor modules are discovered and registered automatically, so adding an integer-keyed entity/action does not require editing the worker loop or batch engine.
- The action payload is persisted as `operation_data`; requests may use the generic `operationData` field, while `updates` remains accepted as a compatibility alias for Contact update clients. Existing PostgreSQL schemas are migrated at startup, including the cursor rename from contact-specific to entity-generic.

### API, inspection, and observability

- Core routes: `GET /bulk-actions`, `POST /bulk-actions`, `GET /bulk-actions/{actionId}`, and `GET /bulk-actions/{actionId}/stats`.
- Additional routes provide filtered entity logs, batch state, pending staged queue-record IDs, and retry.
- Statistics include total, staged, processed, success, failed, skipped, pending, in-progress, completion percentages, elapsed time, and measured average records per second.
- `app.inspect_db` displays database contacts, actions, batches, and logs. `app.verify_action` checks persisted results and worker distribution. `watch_action.py` polls action progress and batches. `load_test.py` submits a real API action and reports elapsed time and throughput.
- A Postman collection covers the core action lifecycle, batch/log inspection, retry, and account rate-limit inspection.

### Dashboard and local worker controls

- A browser dashboard is served at `/`. It refreshes database-backed totals and action progress, shows recent batch assignments and outcomes, and can list up to 100 pending staged entity IDs.
- The dashboard can create a contact update action and reports source records, completed/in-progress/pending percentages, and average throughput from persisted state.
- Optional local worker-process controls are available behind `ENABLE_LOCAL_WORKER_CONTROL`. The control defaults off. Worker output is written under `worker-logs/`.
- The persisted queue is the database batch table. Redis is not configured or used by this implementation.

### Setup and documentation

- Windows development can run without Docker using Python, PostgreSQL, the FastAPI app, and one or more workers.
- PostgreSQL is documented as the configuration for horizontal scaling. Docker Compose defines a PostgreSQL service and separately scalable worker service.
- Seed data is inserted in chunks of 2,000; the seed utility accepts large counts, including `--count 1000000`.
- README and architecture documentation describe setup, processing flow, retries, inspection, verification, and throughput commands.

## Historical validation completed before PostgreSQL-only change

The following local correctness and timing runs used the former SQLite development mode. They are retained as historical observations, but they do not validate the current PostgreSQL-only runtime or multi-worker behavior.

- Python modules compiled successfully, and the FastAPI app imported with its routes registered.
- A temporary single-worker SQLite engine run selected five matching contacts, processed all five successfully in three batches, and finished with no pending records.
- A temporary 5,000-contact SQLite worker-engine benchmark used batch size 250. All 5,000 records succeeded with zero failures or skips in approximately 3.8 seconds, about 79,000 records per minute for that run. This was an in-process, single-worker engine benchmark; it did not include HTTP/API overhead and is not evidence of horizontal scaling.
- An earlier SQLite verification run processed 750 selected records in nine batches and passed the project verifier.
- A Python API-import check passed. A later HTTP-level smoke attempt could not reach a locally started Uvicorn server in the Codex execution environment, so that run did not verify the HTTP path end to end.

## PostgreSQL verification completed

- PostgreSQL is the only supported runtime database. SQLite URL configuration is rejected, SQLite-specific engine setup and type variants are removed, and local setup documentation now requires PostgreSQL.
- The local `.env` is configured for the PostgreSQL server, and the `bulk_actions` database plus application schema were initialized.
- A 1-worker and a 4-worker API-to-worker run each processed 3,000 selected contacts from a 4,000-contact account in 60 batches of 50. Both finished with 3,000 successes and zero failures, skips, or pending records. Both passed `app.verify_action`. The seeded accounts were removed from PostgreSQL after recording the evidence.
- Four workers each claimed 15 batches. The four-worker run took 5.961 seconds versus 9.724 seconds for one worker (1.63x in this single local trial). Full measurements and limitations are in [scaling-results.md](scaling-results.md).
- After the processor-boundary refactor, a four-worker trial selected and processed all 100,000 seeded contacts in 200 batches of 500. It completed in 160.375 seconds at 623.54 records/second, with 100,000 successes and zero failures, skips, or pending records. `app.verify_action` passed, and all four workers received batches. The trial used FastAPI `TestClient` because this execution sandbox could not reach the local Uvicorn port over TCP; see [scaling-results.md](scaling-results.md) for the exact distribution and limits.
- Python source compilation, FastAPI import, and rejection of a SQLite `DATABASE_URL` were checked.
- PostgreSQL failure-path checks passed for a transient infrastructure retry, retry exhaustion, expired worker-lease recovery, and record-level database constraint failures. A separate one-shot helper is now included to reproduce a worker loss after claiming a batch.

## Not yet verified or complete

- **Performance test repeatability:** a 100,000-record four-worker trial is now recorded, but it is a single run and does not compare one versus four workers at the same dataset size. Repeat comparable runs before making stable speedup/capacity claims; no million-record trial has been run.
- **Fresh user trial:** PostgreSQL application tables have been cleared after the recorded trial. The next run should load a new dataset; the rate limit is account-wide and may cap single-account throughput at 10,000 events per UTC minute.
- **Entity-agnostic boundary:** shared worker execution is processor-driven. The dashboard and verifier still have Contact-specific views, which is appropriate for the initial entity. Generic entity result IDs currently use `BIGINT`; supporting CRM entities with non-integer IDs would require changing the log persistence contract.
- **Per-record processing state:** entity logs use `PENDING`, `SUCCESS`, `FAILED`, and `SKIPPED`. In-progress counts are inferred from running batches; `PROCESSING` is not persisted per record.
- **Crash recovery granularity:** one batch is committed as a single transaction. A crash before commit rolls back the whole batch, so successful records from a partially processed but uncommitted batch are retried together rather than retained as partial successes.
- **Optional enhancements:** account rate limiting is implemented at 10,000 entity events per UTC minute. Email deduplication and scheduling remain unimplemented; their design approaches should be documented if claimed as considered enhancements.
- **Submission/demo items:** no Loom architecture/demo video has been recorded. The Postman collection does not yet include the added dashboard, local worker-control, or queue-record endpoints.
- **Dashboard cost at large scale:** live statistics are calculated from persisted rows on refresh. Benchmark monitoring overhead at larger record counts before using the dashboard during an official throughput run.

## Scaling evidence follow-up

1. Repeat the equivalent one-worker and four-worker API-to-worker runs several times to reduce timing noise.
2. Increase the dataset progressively and record PostgreSQL resource use, queue lag, and dashboard overhead.
3. Keep the current trial clearly labeled as local evidence and avoid presenting its 1.63x speedup as a general guarantee.

### Current local benchmark setup note

The PostgreSQL connection and multi-worker flow were verified on the installed Windows instance. The 100,000-record trial evidence is retained in `scaling-results.md`; trial records were cleared afterward so the user can load a fresh dataset.
