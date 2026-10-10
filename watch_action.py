"""Watch one bulk action's statistics and batch ownership through the API."""

import argparse
import json
import time
from urllib.request import urlopen


def get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action_id")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    args = parser.parse_args()
    base = args.api_url.rstrip("/") + f"/bulk-actions/{args.action_id}"
    displayed: dict[str, tuple] = {}

    while True:
        action = get_json(base)
        stats = get_json(base + "/stats")
        page = get_json(base + "/batches?limit=500&offset=0")
        batches = list(page["items"])
        while len(batches) < page["count"]:
            next_page = get_json(base + f"/batches?limit=500&offset={len(batches)}")
            batches.extend(next_page["items"])
        print(
            f"{action['status']} {stats['processed']}/{stats['total']} "
            f"success={stats['success']} failed={stats['failed']} skipped={stats['skipped']} "
            f"progress={stats['progress']}%",
            flush=True,
        )
        for batch in batches:
            state = (
                batch["status"],
                batch["workerId"],
                batch["success"],
                batch["failed"],
                batch["skipped"],
                batch["pending"],
            )
            if displayed.get(batch["batchId"]) != state:
                print(
                    f"  {batch['batchId']} {batch['status']} worker={batch['workerId'] or '-'} "
                    f"items={batch['itemCount']} "
                    f"ok/fail/skip/pending={batch['success']}/{batch['failed']}/{batch['skipped']}/{batch['pending']}",
                    flush=True,
                )
                displayed[batch["batchId"]] = state
        if args.once or action["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(max(args.interval, 0.1))


if __name__ == "__main__":
    main()
