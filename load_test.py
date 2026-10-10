"""Submit one real bulk action and report observed throughput from API statistics."""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the API: {exc.reason}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--account-id", default="A001")
    parser.add_argument(
        "--filter-status",
        default="active",
        help='Contact status to match, or "all" to select every contact in the account.',
    )
    parser.add_argument("--update-status", default="inactive")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    endpoint = args.api_url.rstrip("/") + "/bulk-actions"
    payload = {
        "accountId": args.account_id,
        "entity": "contact",
        "action": "update",
        "filter": {} if args.filter_status.casefold() == "all" else {"status": args.filter_status},
        "operationData": {"status": args.update_status},
    }
    started = time.monotonic()
    action = request_json(endpoint, method="POST", payload=payload)
    action_id = action["actionId"]
    deadline = started + args.timeout_seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request_json(f"{endpoint}/{action_id}/stats")
        state = request_json(f"{endpoint}/{action_id}")
        if state["status"] in {"COMPLETED", "FAILED"}:
            elapsed = max(time.monotonic() - started, 0.001)
            total = latest["total"]
            result = {
                "actionId": action_id,
                "status": state["status"],
                "matchedEntities": total,
                "success": latest["success"],
                "failed": latest["failed"],
                "skipped": latest["skipped"],
                "elapsedSeconds": round(elapsed, 3),
                "entitiesPerSecond": round(total / elapsed, 2),
                "entitiesPerMinute": round(total / elapsed * 60, 2),
            }
            print(json.dumps(result, indent=2))
            if state["status"] == "FAILED":
                raise SystemExit(1)
            return
        time.sleep(args.poll_seconds)
    raise SystemExit(f"Timed out waiting for {action_id}; last stats: {json.dumps(latest)}")


if __name__ == "__main__":
    main()
