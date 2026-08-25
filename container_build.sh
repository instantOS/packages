#!/bin/bash
set -e

# This script runs INSIDE the container

# Trap to restore permissions on exit (success or failure)
cleanup() {
    if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
        echo "Restoring ownership to $HOST_UID:$HOST_GID..."
        chown -R "$HOST_UID:$HOST_GID" /pkg
        chown -R "$HOST_UID:$HOST_GID" /repo
    fi
}
trap cleanup EXIT

# 1. Configure local repo if it exists
# This allows packages to depend on previously built packages
if [ -f /repo/instant.db.tar.gz ]; then
    echo "Adding local repo to pacman.conf"
    echo "[instant]" >>/etc/pacman.conf
    echo "SigLevel = Optional TrustAll" >>/etc/pacman.conf
    echo "Server = file:///repo" >>/etc/pacman.conf
fi

# 2. Make the signing key known to pacman's own keyring
# Once signing is enabled the local file:// repo database is signed, and the
# dependency sync below would fail on the unknown key otherwise.
if [ -n "$GPG_PRIVATE_KEY" ]; then
    printf '%s' "$GPG_PRIVATE_KEY" | base64 -d >/tmp/signing-key.asc
    gpg --batch --import /tmp/signing-key.asc
    gpg --export "$GPG_KEYID" >/tmp/instantos-pubkey.gpg
    # The container image ships without a local pacman secret key
    pacman-key --init
    pacman-key --add /tmp/instantos-pubkey.gpg
    pacman-key --lsign-key "$GPG_KEYID"
    rm -f /tmp/signing-key.asc /tmp/instantos-pubkey.gpg
fi

# Install dependencies
# We sync (-y) to pick up the local repo if added
# gnupg is needed by makepkg/repo-add when signing
pacman -Syu --noconfirm --needed base-devel git sudo gnupg

# Create a builder user with the host's UID/GID
# This ensures files created by makepkg are owned by the host user
if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
    groupadd -g "$HOST_GID" builder_group || true
    useradd -u "$HOST_UID" -g "$HOST_GID" -m builder
else
    useradd -m builder
fi

echo "builder ALL=(ALL) NOPASSWD: ALL" >>/etc/sudoers

# Import the package signing key if the CI provided one.
# The key must be passphrase-less so makepkg and repo-add can call gpg
# non-interactively.
signing_opts=()
if [ -n "$GPG_PRIVATE_KEY" ]; then
    echo "Importing package signing key..."
    printf '%s' "$GPG_PRIVATE_KEY" | base64 -d | sudo -u builder gpg --batch --import
    # makepkg reads the signing key (GPGKEY) from makepkg.conf
    echo "GPGKEY=$GPG_KEYID" >>/etc/makepkg.conf
    signing_opts=(--sign)
fi

# Set permissions for the working directory
# We assume the package source is mounted at /pkg
# Since we matched UIDs, we might not need chown if the mount is correct,
# but chown ensures the builder can write if the host dir was root-owned for some reason.
chown -R builder: /pkg
chown -R builder: /repo

# Switch to builder user and build
cd /pkg
# Limit Rust build parallelism to avoid OOM in CI
sudo -u builder CARGO_BUILD_JOBS=1 makepkg -s --noconfirm "${signing_opts[@]}"

# Move built packages to the repo mount
# We assume the repo is mounted at /repo
mv *.pkg.tar.zst /repo/
if [ -n "$GPG_PRIVATE_KEY" ]; then
    mv *.pkg.tar.zst.sig /repo/
fi

# Update the repository database so subsequent builds can find this package
cd /repo
# We add the just-built packages to the DB
if [ -n "$GPG_PRIVATE_KEY" ]; then
    # Runs as builder so gpg finds the imported signing key
    sudo -u builder repo-add --sign --key "$GPG_KEYID" instant.db.tar.gz *.pkg.tar.zst
else
    repo-add instant.db.tar.gz *.pkg.tar.zst
fi
