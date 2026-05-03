"""Generate a bcrypt hash for ADMIN_PASSWORD_HASH in .env.

Usage:
    python -m scripts.hash_password
"""
import getpass

from app.core.security import hash_password


def main():
    pw1 = getpass.getpass("New admin password: ")
    pw2 = getpass.getpass("Confirm:            ")
    if pw1 != pw2:
        print("Passwords don't match.")
        return
    if len(pw1) < 8:
        print("Use at least 8 characters.")
        return
    h = hash_password(pw1)
    print()
    print("Copy this line into your .env (replace any existing ADMIN_PASSWORD_HASH):")
    print()
    print(f"ADMIN_PASSWORD_HASH={h}")


if __name__ == "__main__":
    main()
