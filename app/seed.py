import argparse

from sqlalchemy import func, select

from app.database import SessionLocal, init_db
from app.models import Account, Contact


def main() -> None:
    parser = argparse.ArgumentParser(description="Insert sample CRM contacts")
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--account-id", default="A001")
    parser.add_argument("--start-index", type=int, default=None)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")

    init_db()
    names = ["Rahul", "Maya", "Arjun", "Ava", "Noah", "Sara", "Ishaan", "Leah"]
    batch_size = 2_000
    inserted = 0
    with SessionLocal() as session, session.begin():
        account = session.get(Account, args.account_id)
        if account is None:
            session.add(Account(id=args.account_id, name=args.account_id))
        start_index = args.start_index
        if start_index is None:
            existing = session.scalar(
                select(func.count()).select_from(Contact).where(Contact.account_id == args.account_id)
            ) or 0
            start_index = existing + 1
        for offset in range(0, args.count, batch_size):
            end = min(offset + batch_size, args.count)
            session.add_all(
                Contact(
                    account_id=args.account_id,
                    name=f"{names[(start_index + index) % len(names)]} {start_index + index}",
                    email=f"contact-{args.account_id.lower()}-{start_index + index}@example.test",
                    age=20 + ((start_index + index) % 45),
                    status="active" if (start_index + index) % 4 else "inactive",
                )
                for index in range(offset, end)
            )
            inserted += end - offset
    print(f"Inserted {inserted} contacts for account {args.account_id}.")


if __name__ == "__main__":
    main()
