import os
import sys

from app import app, db, User, create_tables_and_admin


def main() -> int:
    username = os.getenv("PROMOTE_USERNAME") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not username:
        print("Usage: python promote_user.py <username>")
        return 1

    with app.app_context():
        create_tables_and_admin()

        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"User not found: {username}")
            return 1

        user.role = "admin"
        user.status = "approved"
        db.session.commit()
        print(f"User promoted to admin: {username}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
