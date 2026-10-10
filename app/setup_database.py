from app.database import init_db


def main() -> None:
    init_db()
    print("Database and application schema are ready.")


if __name__ == "__main__":
    main()
