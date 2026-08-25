#!/bin/bash
set -e

# This script runs on the HOST (the CI assemble job).
# It builds the final repository database from the package files in repo/:
# the build matrix jobs upload bare package files (plus signatures), and this
# step turns them into the published repository, signing the database when
# signing is configured.

if [ -n "${GPG_PRIVATE_KEY:-}" ] && [ -z "${GPG_KEYID:-}" ]; then
    echo "ERROR: GPG_PRIVATE_KEY is set but GPG_KEYID (signing subkey id) is not." >&2
    exit 1
fi

mkdir -p repo

if ! ls repo/*.pkg.tar.zst >/dev/null 2>&1; then
    echo "ERROR: no packages found in repo/" >&2
    exit 1
fi

echo "Creating assembler image..."
docker build -q -t instantos-assembler - <<EOF
FROM archlinux:base-devel
RUN pacman -Syu --noconfirm --needed gnupg
EOF

if [ -n "${GPG_PRIVATE_KEY:-}" ]; then
    docker run --rm \
        -e GPG_PRIVATE_KEY \
        -e GPG_KEYID \
        -e HOST_UID="$(id -u)" \
        -e HOST_GID="$(id -g)" \
        -v "$(pwd)/repo:/repo" \
        instantos-assembler bash -c '
set -e
printf "%s" "$GPG_PRIVATE_KEY" | base64 -d | gpg --batch --import
repo-add --sign --key "$GPG_KEYID" /repo/instant.db.tar.gz /repo/*.pkg.tar.zst
chown -R "$HOST_UID:$HOST_GID" /repo
'
else
    docker run --rm \
        -e HOST_UID="$(id -u)" \
        -e HOST_GID="$(id -g)" \
        -v "$(pwd)/repo:/repo" \
        instantos-assembler bash -c '
set -e
repo-add /repo/instant.db.tar.gz /repo/*.pkg.tar.zst
chown -R "$HOST_UID:$HOST_GID" /repo
'
fi

echo "Repository assembled:"
ls -la repo/
