"""SQLite index layer.

The .md files on disk are the only source of truth: notes_fts powers full-text
search and notes_state keeps (mtime, size) per note for incremental sync. Both
tables carry the owner name, and the whole index can be rebuilt at any time.
"""

from __future__ import annotations

import html
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import file_ops

DB_PATH = file_ops.DATA_DIR / "index.db"
MAX_RESULTS = 20
SNIPPET_TOKENS = 14

_lock = threading.RLock()

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    owner UNINDEXED,
    path UNINDEXED,
    title,
    content,
    tokenize = 'unicode61'
);
CREATE TABLE IF NOT EXISTS notes_state (
    owner TEXT NOT NULL,
    path TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    PRIMARY KEY (owner, path)
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _has_owner_column(conn: sqlite3.Connection) -> bool:
    try:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(notes_fts)")]
    except sqlite3.Error:
        return False
    return "owner" in columns


def init_db() -> bool:
    """Create or upgrade the index schema; returns True if an old schema was dropped."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    rebuilt = False
    with _lock, connect() as conn:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notes_fts'"
        ).fetchone():
            if not _has_owner_column(conn):
                conn.executescript(
                    "DROP TABLE IF EXISTS notes_fts; DROP TABLE IF EXISTS notes_state;"
                )
                rebuilt = True
        conn.executescript(SCHEMA)
    return rebuilt


def _derive_title(path: str, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
        elif stripped:
            break
    name = Path(path).name
    return name[: -len(file_ops.NOTE_SUFFIX)] if name.endswith(file_ops.NOTE_SUFFIX) else name


def _delete_rows(conn: sqlite3.Connection, owner: str, path: str) -> None:
    rowids = [
        row[0]
        for row in conn.execute(
            "SELECT rowid FROM notes_fts WHERE owner = ? AND path = ?", (owner, path)
        )
    ]
    for rowid in rowids:
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (rowid,))


def _store_state(conn: sqlite3.Connection, owner: str, path: str) -> None:
    root = file_ops.user_root(owner)
    try:
        stat = (root / path).stat()
    except OSError:
        conn.execute("DELETE FROM notes_state WHERE owner = ? AND path = ?", (owner, path))
        return
    conn.execute(
        """
        INSERT INTO notes_state (owner, path, mtime, size) VALUES (?, ?, ?, ?)
        ON CONFLICT(owner, path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size
        """,
        (owner, path, stat.st_mtime, stat.st_size),
    )


def upsert_note_index(owner: str, path: str, title: str | None, content: str) -> None:
    resolved_title = (title or "").strip() or _derive_title(path, content)
    with _lock, connect() as conn:
        _delete_rows(conn, owner, path)
        conn.execute(
            "INSERT INTO notes_fts (owner, path, title, content) VALUES (?, ?, ?, ?)",
            (owner, path, resolved_title, content),
        )
        _store_state(conn, owner, path)


def delete_note_index(owner: str, path: str) -> None:
    """Delete index rows; when path is a folder, remove its children too."""
    prefix = f"{path.rstrip('/')}/"
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT path FROM notes_fts WHERE owner = ? AND (path = ? OR path LIKE ?)",
            (owner, path, f"{prefix}%"),
        ).fetchall()
        for row in rows:
            _delete_rows(conn, owner, row["path"])
        conn.execute(
            "DELETE FROM notes_state WHERE owner = ? AND (path = ? OR path LIKE ?)",
            (owner, path, f"{prefix}%"),
        )


def delete_owner_index(owner: str) -> None:
    with _lock, connect() as conn:
        rowids = [
            row[0] for row in conn.execute("SELECT rowid FROM notes_fts WHERE owner = ?", (owner,))
        ]
        for rowid in rowids:
            conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (rowid,))
        conn.execute("DELETE FROM notes_state WHERE owner = ?", (owner,))


def rename_path_index(owner: str, old_path: str, new_path: str) -> None:
    """Rename index rows; folder renames rewrite every child prefix."""
    old_prefix = f"{old_path.rstrip('/')}/"
    new_prefix = f"{new_path.rstrip('/')}/"
    if old_path == new_path:
        return
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT path, title, content FROM notes_fts WHERE owner = ? AND (path = ? OR path LIKE ?)",
            (owner, old_path, f"{old_prefix}%"),
        ).fetchall()
        for row in rows:
            mapped = (
                new_path + row["path"][len(old_path) :] if row["path"] != old_path else new_path
            )
            _delete_rows(conn, owner, row["path"])
            conn.execute(
                "INSERT INTO notes_fts (owner, path, title, content) VALUES (?, ?, ?, ?)",
                (owner, mapped, row["title"], row["content"]),
            )
        conn.execute(
            "DELETE FROM notes_state WHERE owner = ? AND (path = ? OR path LIKE ?)",
            (owner, old_path, f"{old_prefix}%"),
        )
        root = file_ops.user_root(owner)
        if root.exists():
            for abs_path in root.rglob(f"*{file_ops.NOTE_SUFFIX}"):
                if abs_path.is_symlink() or not abs_path.is_file():
                    continue
                rel = abs_path.relative_to(root).as_posix()
                if rel == new_path or rel.startswith(new_prefix):
                    _store_state(conn, owner, rel)


def sync_all_from_disk(owner: str) -> dict[str, int]:
    """Compare one user's disk with the index; returns updated/removed counts."""
    root = file_ops.user_root(owner)
    disk: dict[str, tuple[float, int]] = {}
    if root.exists():
        for abs_path in root.rglob(f"*{file_ops.NOTE_SUFFIX}"):
            if abs_path.is_symlink() or not abs_path.is_file():
                continue
            relative = abs_path.relative_to(root)
            if any(
                part.startswith(".") or not file_ops.is_displayable_name(part)
                for part in relative.parts
            ):
                continue
            try:
                stat = abs_path.stat()
            except OSError:
                continue
            disk[relative.as_posix()] = (stat.st_mtime, stat.st_size)

    updated = 0
    removed = 0
    with _lock, connect() as conn:
        indexed = {
            row["path"]: (row["mtime"], row["size"])
            for row in conn.execute(
                "SELECT path, mtime, size FROM notes_state WHERE owner = ?", (owner,)
            )
        }
        for path, meta in disk.items():
            if indexed.get(path) == meta:
                continue
            try:
                content = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _delete_rows(conn, owner, path)
            conn.execute(
                "INSERT INTO notes_fts (owner, path, title, content) VALUES (?, ?, ?, ?)",
                (owner, path, _derive_title(path, content), content),
            )
            _store_state(conn, owner, path)
            updated += 1
        for path in indexed:
            if path not in disk:
                _delete_rows(conn, owner, path)
                conn.execute("DELETE FROM notes_state WHERE owner = ? AND path = ?", (owner, path))
                removed += 1
    return {"updated": updated, "removed": removed}


def _fts_expression(query: str) -> str:
    """Turn user input into a safe FTS5 query: quote every token so * - : cannot break the syntax."""
    tokens = [token for token in re.split(r"\s+", query.replace('"', " ").strip()) if token]
    safe: list[str] = []
    for token in tokens:
        filtered = re.sub(r"[^\w\u4e00-\u9fff]+", "", token, flags=re.UNICODE)
        if filtered:
            safe.append(f'"{filtered}"')
    return " ".join(safe)


def search_notes(owner: str, query: str) -> list[dict]:
    """FTS5 MATCH + snippet(), 20 results max; falls back to LIKE when FTS misses.

    unicode61 treats a run of CJK characters as one token, so “todo” never matches
    inside “todo list” unless it is a prefix. When FTS returns nothing we retry with
    a LIKE substring scan so CJK queries still work.
    """
    term = (query or "").strip()
    expression = _fts_expression(term)
    if not expression:
        return []

    sql = """
        SELECT path, title,
               snippet(notes_fts, 3, '<mark>', '</mark>', '…', ?) AS snippet
        FROM notes_fts
        WHERE notes_fts MATCH ? AND owner = ?
        ORDER BY rank
        LIMIT ?
    """
    with _lock, connect() as conn:
        try:
            rows = conn.execute(sql, (SNIPPET_TOKENS, expression, owner, MAX_RESULTS)).fetchall()
        except sqlite3.Error:
            rows = []
        results: list[dict] = []
        for row in rows:
            snippet = html.escape(row["snippet"] or "", quote=False)
            snippet = snippet.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")
            results.append({"path": row["path"], "title": row["title"], "snippet": snippet})
        if results:
            return results
        return _like_search(conn, owner, term)


def _like_search(conn: sqlite3.Connection, owner: str, term: str) -> list[dict]:
    if not term:
        return []
    escaped_term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_term}%"
    rows = conn.execute(
        """
        SELECT path, title, content FROM notes_fts
        WHERE owner = ? AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
        ORDER BY path
        LIMIT ?
        """,
        (owner, pattern, pattern, MAX_RESULTS),
    ).fetchall()
    return [
        {
            "path": row["path"],
            "title": row["title"],
            "snippet": _highlight(row["content"] or "", term),
        }
        for row in rows
    ]


def _highlight(content: str, term: str, radius: int = 40) -> str:
    """Take a short excerpt around the term, escape HTML first, then add <mark>."""
    lowered = content.lower()
    needle = term.lower()
    index = lowered.find(needle)
    if index < 0:
        excerpt = content[: radius * 2]
    else:
        start = max(0, index - radius)
        end = min(len(content), index + len(term) + radius)
        excerpt = (
            ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")
        )

    escaped = html.escape(excerpt, quote=False)
    escaped_term = html.escape(term, quote=False)
    if not escaped_term:
        return escaped
    pattern = re.compile(re.escape(escaped_term), re.IGNORECASE)
    return pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", escaped)
