# PostgreSQL Worker Scaling Results

## Run details

- Date: 2026-10-10
- Database: local PostgreSQL on Windows, no Docker
- Dataset per run: 4,000 seeded contacts on a fresh account
- Filter: `status=active` (3,000 contacts selected)
- Action: update selected contacts to `status=inactive`
- Batch size: 50; each action created 60 batches
- Worker polling interval: 0.05 seconds
- Timing: wall time from the worker processes being launched until the API reported a terminal action state. Seed and action creation are excluded. The monitor polls every 0.1 seconds.
- Artificial processing delay: none

## Results

| Workers | Selected and successful | Failed / skipped / pending | Batches by worker | Elapsed | Throughput | Relative speed |
| ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 3,000 / 3,000 | 0 / 0 / 0 | 60 | 9.724 s | 308.5 records/s | 1.00x |
| 4 | 3,000 / 3,000 | 0 / 0 / 0 | 15 each across all 4 workers | 5.961 s | 503.2 records/s | 1.63x |

The four-worker run completed approximately 1.63 times as fast in this single local trial. Both runs passed `app.verify_action`; all selected contacts were updated, and action logs and batch counts reconciled. The four-worker case demonstrated concurrent claiming with even batch distribution.

## Limits

This is one 4,000-contact run at each worker count on one local machine. It is useful evidence that workers compete safely and that this workload improved with four workers, but it is not a general throughput guarantee. Repeat runs, larger datasets, and production-like database resources are needed for a stable capacity estimate. The benchmark accounts and records were removed from PostgreSQL after verification; the measured evidence remains here.

## 100,000-record four-worker trial

- Date: 2026-10-10
- Database: local PostgreSQL on Windows, no Docker
- Dataset: 100,000 contacts seeded for account `TRIAL100K`; all 100,000 were selected
- Action: update `status` to `processed`
- Batch size: 500; selection page size: 1,000; 200 batches
- Workers: four independent worker processes using PostgreSQL claims
- API path: FastAPI `TestClient` in-process because the Codex execution sandbox could not connect to the local Uvicorn port; this measures the API route and database-backed workers, without TCP/network overhead
- Result: 100,000 successes, zero failures, zero skips, zero pending; 160.375 seconds observed; 623.54 records/second (37,412.31 records/minute)
- Batch assignments: `trial-100k-1` 53, `trial-100k-2` 39, `trial-100k-3` 65, `trial-100k-4` 43
- Verification: `app.verify_action` passed; all 200 batches completed; all 100,000 contacts had the requested status

This trial demonstrates that the four workers distributed and completed a 100,000-record workload before the account rate limiter was added. It is a single local run, not a production capacity guarantee or a same-size one-versus-four speedup comparison. The trial rows were cleared afterward so the user can start from an empty database.

## Verification

- One worker: action `BA-211CD9193EAF`, account `MW13393BD9`.
- Four workers: action `BA-1407D07F2EFA`, account `MW40EDF3B2`.
- Both verifier runs reported `VERIFICATION PASSED` with 3,000 successes and zero failures, skips, or pending entities.
- Four-worker assignments: `scale-4w-1` 15 batches, `scale-4w-2` 15, `scale-4w-3` 15, and `scale-4w-4` 15.
