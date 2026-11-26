# Contributing to Flow Notes

Thanks for taking the time to send a patch. The project is small and reviewed by one person
in their spare time, so a focused change with a clear commit message goes a long way.

## What lives where

| Path | Contents |
| --- | --- |
| `app/file_ops.py` | Every filesystem path goes through `resolve_safe_path`. Per-user roots, atomic writes, renames. |
| `app/database.py` | SQLite index: `users`, `notes_fts` (FTS5) and `notes_state`. Rebuildable from disk at any time. |
| `app/users.py` | Accounts, scrypt hashing, `token_version`, the delete-to-trash flow. |
| `app/main.py` | FastAPI routes, auth dependency, setup wizard, uploads, export. |
| `app/welcome.py` | The seed note a new account receives, in the language of its first sign-in. |
| `app/cli.py` | Rescue tool: `list`, `passwd`, `create`. |
| `app/templates/` | `index.html`, the single-page shell that also holds the settings view, plus the login/setup pages inlined in `main.py`. |
| `static/` | `app.js` (router, tree, editor glue), `editor.js`, `settings.js` (loaded on demand), `ui.js`, `i18n.js`, `vendor/app.css`, the prebuilt `vendor/editor.bundle.js`. |
| `vendor-build/` | esbuild script that produces `editor.bundle.js`. Build-time only. |
| `scripts/` | CI helpers; `prune-runs.sh` keeps the Actions tab small. |
| `docs/screenshots/` | The images used by the README. |
| `README.md`, `DOCS.md` | User and technical documentation, in English. |

## Requirements

- Python 3.11 or newer. The image pins 3.11; the app has also been run locally on 3.14.
- Node.js 20 for `vendor-build/` only. The committed bundle means you do not need Node to
  run the app.
- Optional: Docker with Compose, `actionlint` for the workflow, a browser for manual checks.

## Build and run

```bash
docker compose up -d --build              # image, port 8000, ./data
docker compose exec -it flownotes python -m app.cli list

# or directly
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The first visit lands on `/setup`; there is no default account.

## Checks before sending a change

There is no unit-test suite yet — the CI job builds the image and runs an end-to-end smoke
test (setup, login, write a note, search, check the files on disk). Locally:

```bash
python3 -m py_compile app/*.py
for f in static/app.js static/editor.js static/settings.js static/ui.js static/i18n.js; do
  node --input-type=module --check < "$f" || exit 1
done
actionlint .github/workflows/*.yml          # if installed
grep -rn -e 'http://' -e 'https://' app static/app.js static/editor.js static/settings.js \
  static/ui.js static/i18n.js static/vendor/app.css Dockerfile docker-compose.yml requirements.txt
```

The last command must print nothing: the app has to stay fully offline. `static/vendor/editor.bundle.js`
is the only file allowed to contain third-party URL strings (ProseMirror error messages and
markdown-it linkify templates).

Rebuilding the editor bundle is only needed when you change the Tiptap dependencies or the
export list:

```bash
cd vendor-build && npm ci && node build.mjs
```

## Formatting

Two config files pin the style: `pyproject.toml` (black, line length 100) and
`.prettierrc.json` (print width 110, double quotes, semicolons). Run both before committing:

```bash
python -m black app/                       # pip install black
npx prettier@3.4.2 --write static/*.js app/templates/*.html
```

`static/vendor/editor.bundle.js` is generated and ignored by `.prettierignore`; `app.css` is
hand-written (short rules stay on one line) and is not run through prettier.

## Screenshots

`docs/screenshots/` is captured by hand from a real browser against a demo account, at
1440x900 (2x DPR) for the desktop shots and 390x844 for the phone one. Refresh them when the
interface changes, and keep every image local: the README must render without network access.

## House rules

- **Comments and backend messages are English.** User-visible text lives only in
  `static/i18n.js`; add the key to all seven locales (en, zh-Hant, zh-Hans, ar, fr, ru, es)
  or the fallback to English hides the gap in review.
- **No external resources.** No CDN, web fonts, icon packs or remote images. Icons are inline
  SVG; the editor dependency is bundled locally.
- **RTL stays working.** Use logical CSS properties (`inset-inline-*`, `padding-inline-*`,
  `border-inline-end`) rather than `left`/`right`; check the Arabic locale when you touch layout.
- **The filesystem is the source of truth.** The database may be dropped and rebuilt; anything
  that only exists in SQLite will be lost.
- **Every path goes through `resolve_safe_path`.** Do not join user input onto a `Path`
  anywhere else.
- **Notes are plain Markdown.** The editor round-trips through `tiptap-markdown`; avoid HTML
  in notes and avoid storing state in the editor that is not in the Markdown.

## Commits and pull requests

- Subject line in the imperative mood, under ~72 characters. The body explains why, not what.
- Keep a pull request to one concern. A refactor plus a behaviour change is two PRs.
- `Signed-off-by` is welcome ([DCO](https://developercertificate.org/)) but CI does not enforce
  it. There is no CLA.

## Licence of contributions

Flow Notes is licensed under the MIT licence (see [LICENSE](LICENSE)). By sending a patch you
agree that it is licensed under the same terms. Third-party components keep their own
licences; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
