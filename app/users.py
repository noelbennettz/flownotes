"""User accounts and passwords.

Accounts live in the same SQLite file as the index (data/index.db). Passwords are
scrypt-hashed with a random salt; token_version invalidates existing cookies when a
password changes or an account is disabled.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass

from . import database, file_ops

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
MIN_PASSWORD = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 1
);
"""


class UserError(ValueError):
    """Invalid account operation (duplicate name, short password, last administrator...)."""


@dataclass(frozen=True)
class User:
    username: str
    is_admin: bool
    disabled: bool
    created_at: float
    token_version: int

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "is_admin": self.is_admin,
            "disabled": self.disabled,
            "created_at": int(self.created_at),
            "token_version": self.token_version,
        }


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        username=row["username"],
        is_admin=bool(row["is_admin"]),
        disabled=bool(row["disabled"]),
        created_at=float(row["created_at"]),
        token_version=int(row["token_version"]),
    )


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DKLEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def ensure_schema() -> None:
    """Create the users table; no account is created automatically. The first administrator comes from /setup or the CLI."""
    with database._lock, database.connect() as conn:
        conn.executescript(SCHEMA)


def count_users() -> int:
    with database._lock, database.connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def create_first_admin(username: str, password: str) -> User:
    """Create the first administrator only when there are no accounts, so it cannot be used to escalate privileges."""
    if count_users() > 0:
        raise UserError("Already initialized")
    return create_user(username, password, is_admin=True)


def get_user(username: str) -> User | None:
    try:
        clean = file_ops.sanitize_username(username)
    except file_ops.PathError:
        return None
    with database._lock, database.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (clean,)).fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[User]:
    with database._lock, database.connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY is_admin DESC, username").fetchall()
    return [_row_to_user(row) for row in rows]


def admin_count() -> int:
    with database._lock, database.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND disabled = 0"
        ).fetchone()["n"]


def authenticate(username: str, password: str) -> User | None:
    user = get_user(username)
    if user is None or user.disabled:
        # Hash even for unknown accounts so response time does not reveal whether a user exists.
        verify_password(password, hash_password("placeholder-password"))
        return None
    with database._lock, database.connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", (user.username,)
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return user


def create_user(username: str, password: str, *, is_admin: bool = False) -> User:
    clean = file_ops.sanitize_username(username)
    if get_user(clean) is not None:
        raise UserError(f"User {clean} already exists")
    password_hash = hash_password(password)
    with database._lock, database.connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, disabled, created_at, token_version) "
            "VALUES (?, ?, ?, 0, ?, 1)",
            (clean, password_hash, 1 if is_admin else 0, time.time()),
        )
    return get_user(clean)  # type: ignore[return-value]


def set_password(username: str, password: str) -> None:
    clean = file_ops.sanitize_username(username)
    if get_user(clean) is None:
        raise UserError("User not found")
    password_hash = hash_password(password)
    with database._lock, database.connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE username = ?",
            (password_hash, clean),
        )


def sign_out_everywhere(username: str) -> None:
    """Invalidate every cookie issued for the account, including the caller's own."""
    clean = file_ops.sanitize_username(username)
    with database._lock, database.connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE username = ?", (clean,)
        )
        if cursor.rowcount == 0:
            raise UserError("User not found")


def set_admin(username: str, is_admin: bool) -> None:
    clean = file_ops.sanitize_username(username)
    user = get_user(clean)
    if user is None:
        raise UserError("User not found")
    if not is_admin and user.is_admin and admin_count() <= 1:
        raise UserError("At least one administrator must remain")
    with database._lock, database.connect() as conn:
        conn.execute(
            "UPDATE users SET is_admin = ? WHERE username = ?", (1 if is_admin else 0, clean)
        )


def set_disabled(username: str, disabled: bool) -> None:
    clean = file_ops.sanitize_username(username)
    user = get_user(clean)
    if user is None:
        raise UserError("User not found")
    if disabled and user.is_admin and admin_count() <= 1:
        raise UserError("At least one active administrator must remain")
    with database._lock, database.connect() as conn:
        conn.execute(
            "UPDATE users SET disabled = ?, token_version = token_version + 1 WHERE username = ?",
            (1 if disabled else 0, clean),
        )


def delete_user(username: str) -> str | None:
    """Delete an account; notes and uploads move to data/trash/<username>-<timestamp>/ so they can be recovered."""
    clean = file_ops.sanitize_username(username)
    user = get_user(clean)
    if user is None:
        raise UserError("User not found")
    if user.is_admin and admin_count() <= 1:
        raise UserError("At least one administrator must remain")

    trash_root = file_ops.DATA_DIR / "trash"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = trash_root / f"{clean}-{stamp}"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    moved = None
    for source, label in (
        (file_ops.user_root(clean), "notes"),
        (file_ops.user_uploads(clean), "uploads"),
    ):
        if source.exists():
            shutil.move(str(source), str(target / label))
            moved = str(target)

    with database._lock, database.connect() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (clean,))
    database.delete_owner_index(clean)
    return moved
