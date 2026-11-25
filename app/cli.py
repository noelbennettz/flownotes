"""flownotes rescue tool: reset a forgotten admin password, list accounts, create the first administrator.

It reads and writes data/index.db directly, so the server does not need to run and the
change takes effect immediately (no restart).

    docker compose exec flownotes python -m app.cli list
    docker compose exec flownotes python -m app.cli passwd admin
    docker compose exec flownotes python -m app.cli create admin
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import database, file_ops, users


def _prepare() -> None:
    """Prepare directories and tables like the server does, so an empty data folder works."""
    file_ops.ensure_dirs()
    database.init_db()
    users.ensure_schema()


def _ask_password() -> str:
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match; nothing changed")
    if len(first) < users.MIN_PASSWORD:
        raise SystemExit(f"Password must be at least {users.MIN_PASSWORD} characters")
    return first


def _cmd_list() -> int:
    rows = users.list_users()
    if not rows:
        print(
            "No accounts yet: open /setup in a browser, or run create to make the first administrator."
        )
        return 0
    for user in rows:
        state = "disabled" if user.disabled else "active"
        role = "administrator" if user.is_admin else "user"
        print(f"{user.username:<20} {role} · {state}")
    return 0


def _cmd_passwd(username: str) -> int:
    clean = username.strip().lower()
    if users.get_user(clean) is None:
        print(f"Account {clean} not found; run list to see existing accounts.", file=sys.stderr)
        return 1
    password = _ask_password()
    users.set_password(clean, password)
    print(f"Password reset for {clean}; earlier sessions are now invalid.")
    return 0


def _cmd_create(username: str) -> int:
    if users.count_users() > 0:
        print(
            "Accounts already exist. Add users from Settings, and use passwd to reset a password.",
            file=sys.stderr,
        )
        return 1
    password = _ask_password()
    created = users.create_first_admin(username, password)
    print(f"Administrator {created.username} created; sign in at /login.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="flownotes rescue tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list every account")

    passwd = sub.add_parser(
        "passwd", help="reset an account password (the old one is not required)"
    )
    passwd.add_argument("username")

    create = sub.add_parser(
        "create", help="create the first administrator (only when there are no accounts)"
    )
    create.add_argument("username")

    args = parser.parse_args(argv)
    _prepare()

    if args.command == "list":
        return _cmd_list()
    if args.command == "passwd":
        return _cmd_passwd(args.username)
    if args.command == "create":
        return _cmd_create(args.username)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
