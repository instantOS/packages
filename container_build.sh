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

# Install dependencies
# We sync (-y) to pick up the local repo if added
pacman -Syu --noconfirm --needed base-devel git sudo

# Create a builder user with the host's UID/GID
# This ensures files created by makepkg are owned by the host user
if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
    groupadd -g "$HOST_GID" builder_group || true
    useradd -u "$HOST_UID" -g "$HOST_GID" -m builder
else
    useradd -m builder
fi

echo "builder ALL=(ALL) NOPASSWD: ALL" >>/etc/sudoers

# Set permissions for the working directory
# We assume the package source is mounted at /pkg
# Since we matched UIDs, we might not need chown if the mount is correct,
# but chown ensures the builder can write if the host dir was root-owned for some reason.
chown -R builder: /pkg
chown -R builder: /repo

# Switch to builder user and build
cd /pkg
# Limit Rust build parallelism to avoid OOM in CI
sudo -u builder CARGO_BUILD_JOBS=1 makepkg -s --noconfirm

# Move built packages to the repo mount
# We assume the repo is mounted at /repo
mv *.pkg.tar.zst /repo/

# Update the repository database so subsequent builds can find this package
cd /repo
# We add the just-built packages to the DB
repo-add instant.db.tar.gz *.pkg.tar.zst
