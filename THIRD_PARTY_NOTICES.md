# Third-party notices — Flow Notes

Flow Notes itself is licensed under the **MIT licence** (see [LICENSE](LICENSE)). It bundles
or installs the components below; each keeps its own licence. None of the bundled code has
been modified, so no notice of changes is due.

Summary of what the container image and the browser bundle carry:

- Python runtime packages: Apache-2.0 ×2, BSD-3-Clause ×7, MIT ×11, PSF-2.0 ×1 (plus the
  packaging tools listed separately below).
- Code inside `static/vendor/editor.bundle.js`: MIT (most), ISC ×1, BSD-2-Clause ×1.
- Build-only tools: esbuild and the Node image, which are **not** present at runtime.
- The runtime base image (`python:3.11-slim`) is Debian bookworm plus CPython, with its own
  licences (GPL-2.0-or-later for some system packages, PSF-2.0 for CPython). None of it is
  linked into the application code.

## Python packages installed in the image

Versions come from `requirements.txt` and its resolved dependencies; the table reflects the
image built from the current `master`.

| Package | Version | Licence |
| --- | --- | --- |
| `aiofiles` | 24.1.0 | Apache-2.0 |
| `annotated-types` | 0.8.0 | MIT |
| `anyio` | 4.16.0 | MIT |
| `click` | 8.5.1 | BSD-3-Clause |
| `fastapi` | 0.115.6 | MIT |
| `h11` | 0.16.0 | MIT |
| `httptools` | 0.9.0 | MIT |
| `idna` | 3.20 | BSD-3-Clause |
| `itsdangerous` | 2.2.0 | BSD-3-Clause |
| `pydantic` | 2.14.4 | MIT |
| `pydantic-core` | 2.50.5 | MIT |
| `python-dotenv` | 1.2.4 | BSD-3-Clause |
| `python-multipart` | 0.0.20 | Apache-2.0 |
| `PyYAML` | 6.0.4 | MIT |
| `starlette` | 0.41.3 | BSD-3-Clause |
| `typing-extensions` | 4.16.0 | PSF-2.0 |
| `typing-inspection` | 0.4.5 | MIT |
| `uvicorn` | 0.34.0 | BSD-3-Clause |
| `uvloop` | 0.23.0 | MIT |
| `watchfiles` | 1.3.0 | MIT |
| `websockets` | 17.4 | BSD-3-Clause |

`pip`, `setuptools`, `wheel` and `packaging` also live in the image because pip installed
everything; they are packaging tools, not part of the running service. `packaging` is
`Apache-2.0 OR BSD-2-Clause`.

To print the same list from an image you built:

```bash
docker run --rm --entrypoint python flownotes:latest -c \
  "import importlib.metadata as m; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or d.metadata.get('License')) for d in m.distributions()]"
```

## Code bundled into `static/vendor/editor.bundle.js`

The editor is compiled once by `vendor-build/build.mjs` and committed, so the browser never
fetches a dependency. Everything below ends up inside that one file.

| Component | Version | Licence |
| --- | --- | --- |
| `@tiptap/core`, `@tiptap/pm`, `@tiptap/starter-kit` | 2.27.3 | MIT |
| `@tiptap/extension-{image,placeholder,task-item,task-list}` | 2.27.3 | MIT |
| `@tiptap/extension-{blockquote,bold,bullet-list,code,code-block,document,dropcursor,gapcursor,hard-break,heading,history,horizontal-rule,italic,list-item,ordered-list,paragraph,strike,text,text-style}` | 2.27.3 | MIT |
| `prosemirror-{changeset,collab,commands,dropcursor,gapcursor,history,inputrules,keymap,markdown,menu,model,schema-basic,schema-list,state,tables,transform,view}` | 1.2–1.8 / 2.4–2.4 | MIT |
| `prosemirror-trailing-node` | 3.0.0 | MIT |
| `@remirror/core-constants` | 3.0.0 | MIT |
| `tiptap-markdown` | 0.8.10 | MIT |
| `markdown-it` | 14.3.3, 15.0.3 | MIT |
| `markdown-it-task-lists` | 2.1.1 | ISC |
| `linkify-it`, `mdurl`, `uc.micro`, `punycode.js` | 5.0–6.1 / 2.x / 2.x–3.x / 2.3.1 | MIT |
| `entities` | 4.5.0, 8.1.0 | BSD-2-Clause |
| `orderedmap`, `rope-sequence`, `w3c-keyname`, `crelt` | current | MIT |

`markdown-it` resolves `argparse` for its command-line entry point; the CLI is not imported by
the bundle, and `argparse` does not appear in `editor.bundle.js`.

## Build-only tools (not shipped)

| Tool | Version | Licence | Purpose |
| --- | --- | --- | --- |
| `esbuild` | 0.25.12 | MIT | Bundles the editor in the `vendor` image stage |
| `node:20-bookworm-slim` | Node 20 | MIT and Debian licences | Build stage only; no Node in the final image |
| `python:3.11-slim` | CPython 3.11 | PSF-2.0 and Debian licences | Runtime base image |

## Keeping this file honest

There is no automated licence gate yet: CI builds the image and runs a smoke test, but does
not scan licences. When you add or bump a dependency, run the command above (or
`npm ls --omit=dev` inside `vendor-build/`) and update this file in the same pull request.
A future `licences` workflow, in the style of the one in [iMail](https://github.com/noelbennettz/imail),
would run `license-checker` over the bundle inputs and `pip-licenses` over the image.
