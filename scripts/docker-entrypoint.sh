#!/bin/sh
# Startup shim for the container image.
#
# ./data is normally a bind mount. Docker creates the mount point (and a host
# directory that does not exist yet) as root, so a container started with a
# plain `user:` cannot write to it and the app dies with
# "Permission denied: '/app/data/notes'". Instead the container starts as root,
# takes ownership of the data directory, and then drops to FLOWNOTES_UID/GID
# before uvicorn runs. That keeps new notes owned by the host user without
# anyone having to chown the directory by hand.
#
# A non-root start is left alone; the app then prints the same permission error
# and the operator can fix the mount or pass FLOWNOTES_UID/GID.
set -eu

data_dir="${FLOWNOTES_DATA:-/app/data}"
target_uid="${FLOWNOTES_UID:-1000}"
target_gid="${FLOWNOTES_GID:-1000}"

if [ "$(id -u)" = "0" ] && [ "$target_uid" != "0" ]; then
    mkdir -p "$data_dir/notes" "$data_dir/uploads"
    chown -R "$target_uid:$target_gid" "$data_dir"
    # Numeric ids work without a passwd entry; --clear-groups avoids inheriting
    # the root group list, matching what `user:` used to provide.
    exec setpriv --reuid "$target_uid" --regid "$target_gid" --clear-groups "$@"
fi

exec "$@"
