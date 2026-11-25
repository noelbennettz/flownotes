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
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("FLOWNOTES_DATA") or Path(__file__).resolve().parent.parent / "data"
).resolve()
NOTES_ROOT = DATA_DIR / "notes"
UPLOADS_ROOT = DATA_DIR / "uploads"

NOTE_SUFFIX = ".md"
MAX_NAME_BYTES = 255
MAX_PATH_CHARS = 1024

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
