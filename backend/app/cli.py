"""Management commands.  Usage inside the api container:

    python -m app.cli seed
    python -m app.cli create-admin --email you@nearbnb.com --name "Your Name"   (prompts for password)
"""
import argparse
import asyncio
import getpass
from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email

from app.core.db import task_session
from app.core.security import hash_password
from app.modules.auth.service import get_user_by_email, normalize_email
from app.modules.legal import service as legal
from app.modules.cancellations.service import seed_templates
from app.modules.pricing.service import seed_tax_rules
from app.modules.properties.admin_router import seed_catalogues
from app.modules.users.models import USER_ACTIVE, User


async def seed() -> None:
    async with task_session() as session:
        created = await legal.seed_placeholders(session)
        catalogue = await seed_catalogues(session) + await seed_tax_rules(session) + await seed_templates(session)
    print(f"Seeded {created} placeholder legal document(s) and {catalogue} property type(s)/amenities.")


async def create_admin(email: str, name: str, password: str) -> None:
    if len(password) < 10:
        raise SystemExit("Password must be at least 10 characters.")
    try:  # same rules as the login form, so an admin can never be created that cannot log in
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise SystemExit(f"Invalid email: {exc}") from exc
    async with task_session() as session:
        existing = await get_user_by_email(session, email)
        if existing:
            existing.is_admin = True
            existing.status = USER_ACTIVE
            existing.email_verified_at = existing.email_verified_at or datetime.now(UTC)
            existing.password_hash = hash_password(password)
            print(f"Updated existing user {existing.email} as admin.")
        else:
            session.add(
                User(
                    email=normalize_email(email), full_name=name, password_hash=hash_password(password),
                    is_admin=True, status=USER_ACTIVE, email_verified_at=datetime.now(UTC),
                )
            )
            print(f"Created admin {normalize_email(email)}.")
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password", help="Omit to be prompted (recommended).")
    args = parser.parse_args()

    if args.command == "seed":
        asyncio.run(seed())
    elif args.command == "create-admin":
        password = args.password or getpass.getpass("Admin password: ")
        asyncio.run(create_admin(args.email, args.name, password))


if __name__ == "__main__":
    main()
