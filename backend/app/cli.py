import argparse
import getpass

from sqlalchemy import select

from app.auth import LOCAL_USER, passwords
from app.config import settings
from app.db import Session, User
from app.skills import seed
from app.storage import Storage


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    create = sub.add_parser("create-user")
    create.add_argument("email")
    args = parser.parse_args()
    if args.command == "seed":
        Storage().check()
        with Session() as db:
            if settings().implicit_session and not db.get(User, LOCAL_USER):
                db.add(User(id=LOCAL_USER, email="local@framecraft.test"))
            seed(db)
        print("Studio skills seeded.")
    else:
        password = getpass.getpass("Password (at least 10 characters): ")
        email = args.email.lower()
        if len(password) < 10:
            parser.error("Password must contain at least 10 characters.")
        with Session.begin() as db:
            if db.scalar(select(User).where(User.email == email)):
                parser.error("A user with this email already exists.")
            db.add(User(email=email, password=passwords.hash(password)))
        print(f"Created user {email}.")


if __name__ == "__main__":
    main()
