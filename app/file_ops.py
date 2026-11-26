"""Note file operations.

Each user owns a root directory (`data/notes/<username>/`) and every public path is
relative to that root. Absolute paths, `..`, control characters, over-long names and
symlinks pointing outside the root are rejected here: resolve_safe_path is the only
place that turns user input into a filesystem path.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("FLOWNOTES_DATA") or Path(__file__).resolve().parent.parent / "data"
).resolve()
NOTES_ROOT = DATA_DIR / "notes"
UPLOADS_ROOT = DATA_DIR / "uploads"

NOTE_SUFFIX = ".md"
MAX_NAME_BYTES = 255
MAX_PATH_CHARS = 1024

# Automatic merging is line-based. SequenceMatcher is quadratic in the worst case, and the
# request runs in a worker thread, so very large notes fall back to keeping both versions.
MERGE_BYTE_LIMIT = 2 * 1024 * 1024
MERGE_LINE_LIMIT = 20000

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")


class PathError(ValueError):
    """Invalid path or user name."""


class NoteConflict(ValueError):
    """The file changed after the client read it; carries the version now on disk."""

    def __init__(self, current_version: str):
        super().__init__("This note changed on another device")
        self.current_version = current_version


# Compare-then-replace has to be atomic, or two writers can both pass the check.
# The app runs as a single process (uvicorn without workers), so a process lock is enough.
_WRITE_LOCK = threading.RLock()


def ensure_dirs() -> None:
    for path in (NOTES_ROOT, UPLOADS_ROOT):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def sanitize_username(value: str) -> str:
    """The user name doubles as a directory name, so keep it to a safe character set."""
    username = (value or "").strip().lower()
    if not _USERNAME.match(username):
        raise PathError(
            "User names must be 2-32 lowercase letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return username


def user_root(username: str) -> Path:
    username = sanitize_username(username)
    root = (NOTES_ROOT / username).resolve()
    if NOTES_ROOT.resolve() not in root.parents:
        raise PathError("User directory escapes the notes root")
    return root


def user_uploads(username: str) -> Path:
    username = sanitize_username(username)
    root = (UPLOADS_ROOT / username).resolve()
    if UPLOADS_ROOT.resolve() not in root.parents:
        raise PathError("Upload directory escapes the root")
    return root


def _check_component(part: str) -> None:
    if part in ("", ".", ".."):
        raise PathError("Invalid file name")
    if _CONTROL_CHARS.search(part):
        raise PathError("File names must not contain newlines or control characters")
    if len(part.encode("utf-8")) > MAX_NAME_BYTES:
        raise PathError("File or folder name too long (255 bytes max)")


def resolve_safe_path(
    rel_path: str, *, root: Path, must_exist: bool = False, allow_root: bool = False
) -> Path:
    raw = "" if rel_path is None else str(rel_path)
    if "\x00" in raw:
        raise PathError("Path contains invalid characters")
    raw = raw.strip().replace("\\", "/")
    if len(raw) > MAX_PATH_CHARS:
        raise PathError("Path too long")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise PathError("Absolute paths are not allowed")

    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PathError("Directory traversal is not allowed")
    if not parts and not allow_root:
        raise PathError("Path must not be empty")
    for part in parts:
        _check_component(part)

    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise PathError("Path escapes the notes directory")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"Not found: {raw}")
    return target


def _rel_of(path: Path, root: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def _is_note_name(name: str) -> bool:
    return name.endswith(NOTE_SUFFIX) and not name.startswith(".")


def is_displayable_name(name: str) -> bool:
    """Names with control characters cannot be opened from the UI (and are rejected on create), so treat them as absent."""
    return not _CONTROL_CHARS.search(name)


def _scan(folder: Path, root: Path) -> list[dict]:
    dirs: list[dict] = []
    files: list[dict] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []

    for entry in entries:
        # Skip hidden files and symlinks: a symlink may point at someone else's directory.
        if entry.name.startswith(".") or not is_displayable_name(entry.name) or entry.is_symlink():
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                dirs.append(
                    {
                        "name": entry.name,
                        "path": _rel_of(Path(entry.path), root),
                        "type": "folder",
                        "mtime": int(entry.stat(follow_symlinks=False).st_mtime),
                        "children": _scan(Path(entry.path), root),
                    }
                )
            elif entry.is_file(follow_symlinks=False) and _is_note_name(entry.name):
                files.append(
                    {
                        "name": entry.name,
                        "path": _rel_of(Path(entry.path), root),
                        "type": "file",
                        "mtime": int(entry.stat(follow_symlinks=False).st_mtime),
                    }
                )
        except OSError:
            continue

    dirs.sort(key=lambda node: node["name"].lower())
    files.sort(key=lambda node: node["name"].lower())
    return dirs + files


def get_tree(root: Path) -> list[dict]:
    """Recursive tree: folders first, siblings sorted by name."""
    if not root.exists():
        return []
    return _scan(root, root)


def list_note_paths(root: Path) -> list[str]:
    """Relative paths of every .md file; skips hidden entries and symlinks, matching get_tree."""
    if not root.exists():
        return []
    paths: list[str] = []
    for path in root.rglob(f"*{NOTE_SUFFIX}"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") or not is_displayable_name(part) for part in relative.parts):
            continue
        paths.append(relative.as_posix())
    return sorted(paths)


def normalize_note_path(rel_path: str) -> str:
    candidate = (rel_path or "").strip().replace("\\", "/").strip("/")
    if not candidate:
        raise PathError("Note path must not be empty")
    if not candidate.endswith(NOTE_SUFFIX):
        candidate = f"{candidate}{NOTE_SUFFIX}"
    return candidate


def normalize_folder_path(rel_path: str) -> str:
    candidate = (rel_path or "").strip().replace("\\", "/").strip("/")
    if not candidate:
        raise PathError("Folder path must not be empty")
    return candidate


def _note_version(path: Path) -> str:
    """Content hash used as the optimistic-concurrency token."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_note(root: Path, rel_path: str) -> tuple[str, str]:
    """Return (content, version); the version is what a later save must quote."""
    path = resolve_safe_path(rel_path, root=root, must_exist=True)
    if not path.is_file():
        raise PathError("Not a file")
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()


def note_meta(root: Path, rel_path: str) -> dict:
    """Cheap check for “did this note change elsewhere?” without sending the content."""
    path = resolve_safe_path(rel_path, root=root, must_exist=True)
    if not path.is_file():
        raise PathError("Not a note file")
    stat = path.stat()
    return {
        "version": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mtime": int(stat.st_mtime),
        "size": stat.st_size,
    }


def _write_temp(path: Path, data: bytes) -> str:
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=NOTE_SUFFIX)
    try:
        with os.fdopen(handle, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return tmp_name


def write_note(
    root: Path,
    rel_path: str,
    content: str,
    *,
    base_version: str | None = None,
    create_only: bool = False,
    must_exist: bool = False,
) -> tuple[str, str]:
    """Create or replace a note and return (normalized path, new version).

    ``base_version`` turns the write into an optimistic-concurrency update: the write is
    refused with NoteConflict when the file no longer has that version. ``create_only``
    fails if the note already exists, and ``must_exist`` refuses to create a new one.
    The compare, the atomic replace and the version all happen under one lock.
    """
    normalized = normalize_note_path(rel_path)
    path = resolve_safe_path(normalized, root=root)
    data = content.encode("utf-8")
    version = hashlib.sha256(data).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    with _WRITE_LOCK:
        existing = path.is_file()
        if create_only and path.exists():
            raise FileExistsError(f"{normalized} already exists")
        if must_exist and not existing:
            raise FileNotFoundError(f"Not found: {normalized}")
        if base_version is not None:
            if not existing:
                raise FileNotFoundError(f"Not found: {normalized}")
            current = _note_version(path)
            if current != base_version:
                raise NoteConflict(current)

        tmp_name = _write_temp(path, data)
        try:
            if create_only:
                # link() fails atomically if another writer created the file meanwhile.
                try:
                    os.link(tmp_name, path)
                except FileExistsError:
                    raise
                except OSError:
                    # Filesystems without hard links: fall back to an exclusive create.
                    with open(path, "xb") as target:
                        target.write(data)
            else:
                os.replace(tmp_name, path)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return normalized, version


def _changes(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    """Edits that turn *base* into *other*: (start, end, text); insertions have start == end."""
    changes: list[tuple[int, int, list[str]]] = []
    matcher = SequenceMatcher(None, base, other, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append((i1, i2, other[j1:j2]))
    return changes


def merge_notes(base: str, mine: str, theirs: str) -> str | None:
    """Line-based three-way merge; None means both sides changed the same lines differently.

    Edits in different places (the common “laptop edits the top, phone edits the bottom” case),
    identical edits and deletions merge cleanly. Two devices appending different text at the
    same spot counts as a conflict, because choosing an order for them would be a guess.
    Line endings, BOMs and a missing final newline are preserved: everything is compared as
    lines with their terminators kept.
    """
    if max(len(base), len(mine), len(theirs)) > MERGE_BYTE_LIMIT:
        return None
    base_lines = base.splitlines(keepends=True)
    mine_lines = mine.splitlines(keepends=True)
    theirs_lines = theirs.splitlines(keepends=True)
    if max(len(base_lines), len(mine_lines), len(theirs_lines)) > MERGE_LINE_LIMIT:
        return None

    mine_changes = _changes(base_lines, mine_lines)
    theirs_changes = _changes(base_lines, theirs_lines)
    merged: list[str] = []
    pos = mi = ti = 0
    total = len(base_lines)

    while pos <= total:
        m_next = mine_changes[mi] if mi < len(mine_changes) else None
        t_next = theirs_changes[ti] if ti < len(theirs_changes) else None

        starts = [change[0] for change in (m_next, t_next) if change is not None]
        if starts and pos < min(starts):
            # Copy the base lines both sides left alone.
            stop = min(starts)
            merged.extend(base_lines[pos:stop])
            pos = stop
            continue

        if m_next is not None and t_next is not None:
            m_start, m_end, m_text = m_next
            t_start, t_end, t_text = t_next
            if m_start == t_start:
                if m_text == t_text:
                    merged.extend(m_text)
                    pos = max(m_end, t_end)
                    mi += 1
                    ti += 1
                    continue
                return None
            if m_start < t_start:
                if m_end > t_start:
                    return None
                merged.extend(m_text)
                pos = m_end
                mi += 1
                continue
            if t_end > m_start:
                return None
            merged.extend(t_text)
            pos = t_end
            ti += 1
            continue

        if m_next is not None:
            merged.extend(m_next[2])
            pos = m_next[1]
            mi += 1
            continue
        if t_next is not None:
            merged.extend(t_next[2])
            pos = t_next[1]
            ti += 1
            continue

        merged.extend(base_lines[pos:])
        break

    return "".join(merged)


def _conflict_name(name: str, stamp: str, counter: int) -> str:
    """Sibling name for a kept copy, kept inside the 255-byte component limit."""
    stem = name[: -len(NOTE_SUFFIX)] if name.endswith(NOTE_SUFFIX) else name
    suffix = f" (conflict {stamp})" + (f"-{counter}" if counter > 1 else "")
    budget = MAX_NAME_BYTES - len(f"{suffix}{NOTE_SUFFIX}".encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return f"{stem}{suffix}{NOTE_SUFFIX}"


def _write_conflict_copy(root: Path, normalized: str, content: str) -> tuple[str, str]:
    """Store one side of a concurrent edit as a sibling note; never replaces an existing file."""
    parent, _, name = normalized.rpartition("/")
    stamp = f"{datetime.now():%Y-%m-%d %H-%M-%S}"
    last_error: Exception | None = None
    for counter in range(1, 101):
        candidate = _conflict_name(name, stamp, counter)
        if parent:
            candidate = f"{parent}/{candidate}"
        try:
            return write_note(root, candidate, content, create_only=True)
        except FileExistsError as exc:
            last_error = exc
    raise FileExistsError("Could not find a free name for the conflict copy") from last_error


def _replace_atomically(path: Path, data: bytes) -> None:
    tmp_name = _write_temp(path, data)
    try:
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def merge_save(
    root: Path,
    rel_path: str,
    content: str,
    *,
    base_version: str,
    base_content: str,
) -> dict:
    """Save with automatic three-way merge when the file moved on since the client read it.

    ``base_content`` is the text the client last read. When it hashes to ``base_version`` the
    server can merge safely; when it does not (a stale or lossy copy, e.g. a file that is not
    valid UTF-8) merging would be a guess, so both versions are kept instead of overwriting.
    Everything happens under the write lock, so no third writer can slip between the compare
    and the replace.

    Returns the note's new state. When both sides touched the same lines, the caller's text is
    kept as a sibling “(conflict …)” note instead of being discarded, and the note itself keeps
    the version that was already on disk. ``missing`` means the note was renamed or deleted
    before this save landed; the text is kept as a copy so nothing is lost.
    """
    normalized = normalize_note_path(rel_path)
    path = resolve_safe_path(normalized, root=root)
    mine = content.encode("utf-8")
    # The base text is only trusted for merging when it hashes to the version the client quotes.
    base_is_verified = hashlib.sha256(base_content.encode("utf-8")).hexdigest() == base_version

    with _WRITE_LOCK:
        if not path.is_file():
            copy_rel, copy_version = _write_conflict_copy(root, normalized, content)
            return {
                "path": normalized,
                "version": None,
                "content": content,
                "merged": False,
                "missing": True,
                "conflict_copy": copy_rel,
                "conflict_copy_version": copy_version,
            }

        current = path.read_bytes()
        current_version = hashlib.sha256(current).hexdigest()
        if current_version == base_version:
            # Nobody else wrote in the meantime; keep the plain save path.
            _replace_atomically(path, mine)
            return {
                "path": normalized,
                "version": hashlib.sha256(mine).hexdigest(),
                "content": content,
                "merged": False,
                "missing": False,
            }

        try:
            theirs = current.decode("utf-8")
        except UnicodeDecodeError:
            # Never rewrite a file we cannot round-trip; keep both versions instead.
            theirs = None

        merged_text = (
            merge_notes(base_content, content, theirs)
            if base_is_verified and theirs is not None
            else None
        )
        if merged_text is None:
            copy_rel, copy_version = _write_conflict_copy(root, normalized, content)
            return {
                "path": normalized,
                "version": current_version,
                "content": (
                    theirs if theirs is not None else current.decode("utf-8", errors="replace")
                ),
                "merged": False,
                "missing": False,
                "conflict_copy": copy_rel,
                "conflict_copy_version": copy_version,
            }

        merged_bytes = merged_text.encode("utf-8")
        merged_version = hashlib.sha256(merged_bytes).hexdigest()
        if merged_version != current_version:
            # Identical edits on both devices already produced this text; skip the write so the
            # file (and its mtime, which drives the index) does not churn for nothing.
            _replace_atomically(path, merged_bytes)
        return {
            "path": normalized,
            "version": merged_version,
            "content": merged_text,
            "merged": True,
            "missing": False,
        }


def create_folder(root: Path, rel_path: str) -> str:
    normalized = normalize_folder_path(rel_path)
    path = resolve_safe_path(normalized, root=root)
    if path.exists():
        raise FileExistsError(f"{normalized} already exists")
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    return normalized


def rename_path(root: Path, old_path: str, new_path: str) -> tuple[str, str]:
    """Rename or move; the same path (or a case-only change) succeeds as a no-op."""
    source = resolve_safe_path(old_path, root=root, must_exist=True)
    target_rel = (
        normalize_note_path(new_path) if source.is_file() else normalize_folder_path(new_path)
    )
    target = resolve_safe_path(target_rel, root=root)
    old_rel = _rel_of(source, root)
    if source == target:
        return old_rel, old_rel
    if target.exists():
        raise FileExistsError(f"{target_rel} already exists")
    if source.is_dir() and source in target.parents:
        raise PathError("Cannot move a folder into itself")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.rename(source, target)
    return old_rel, _rel_of(target, root)


def delete_path(root: Path, rel_path: str) -> str:
    path = resolve_safe_path(rel_path, root=root, must_exist=True)
    relative = _rel_of(path, root)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return relative
