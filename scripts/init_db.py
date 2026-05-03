"""Initialize SQLite schema. Run once before first start.

Usage:
    python -m scripts.init_db
"""
from app.core import db


def main():
    db.init_db()
    print(f"✓ Database initialized.")


if __name__ == "__main__":
    main()
