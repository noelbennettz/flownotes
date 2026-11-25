# Security policy

Flow Notes is a self-hosted note server: it authenticates users, serves a Markdown
editor, stores notes as plain files per account, indexes them with SQLite FTS5 and accepts
image uploads. It is usually reachable on a LAN or through a reverse proxy, so reports
are welcome.

## Supported versions

Only the current `master` branch is supported. It is published as
`ghcr.io/noelbennettz/flownotes:latest` (also tagged `master` and `sha-<commit>`).
Older images and forks get fixes only if the report also reproduces on the current
`master`.

## Reporting a vulnerability

Use GitHub's private reporting: the **Security** tab of
<https://github.com/noelbennettz/flownotes>, then **Report a vulnerability**. The report
stays private until an advisory is published.

If that form is not available, open a normal issue titled exactly `Security contact request`
and put nothing in the body about the technical details, the affected endpoints, the
reproduction steps or the impact. A maintainer will open a private channel there.

Please do not open a public issue with the details, and do not test against deployments you
do not own.

## What to expect

There is no response-time guarantee: this is a volunteer-maintained project without a
support contract or a security team on call. Reports are read and answered in the order they
arrive. A report that includes the affected commit, a reproduction on a fresh deployment and
the impact you observed is much easier to act on, and therefore usually fixed sooner.

## In scope

- Authentication and sessions: the login form, the setup wizard, the signed cookie,
  `token_version` invalidation, and the login rate limit.
- Per-user isolation: reading, renaming, moving or deleting another account's notes,
  images or index rows.
- Path handling: `resolve_safe_path`, directory traversal, symlink escapes, control
  characters and over-long names.
- The editor surfaces that take attacker-controlled input: Markdown rendering, the search
  `snippet()` output with its `<mark>` tags, and file names shown in the tree.
- Image uploads: the MIME allow-list, the 20 MB cap, the generated file names and the
  authenticated `/uploads/<owner>/<file>` route.
- The container image: anything that lets an attacker who can reach the container gain code
  execution, read another account's data, or escape the mounted `data/` directory.

## Out of scope

- Anyone who already has shell access to the host or the Docker socket. That is equivalent to
  owning the deployment.
- TLS, reverse-proxy configuration, firewall rules and port exposure. Flow Notes serves plain
  HTTP; a TLS terminator is expected in front of it.
- The `secure=False` cookie flag: it is deliberate, so that a plain-HTTP LAN address can log
  in. Exposing the port to the internet without TLS is a deployment mistake, not a
  vulnerability report.
- Weak or reused passwords chosen by users, and the `FLOWNOTES_SETUP_TOKEN` being left unset
  while `/setup` is reachable by strangers.
- Vulnerabilities in upstream dependencies (FastAPI, Starlette, Tiptap/ProseMirror,
  markdown-it, SQLite). Report those upstream; this project picks up the fixed release.

## Scope of this document

This is the security policy of the project, not a warranty. It describes how reports are
handled; the security notes in [DOCS.md](DOCS.md#security-notes) describe behaviour you can
check against the code.
