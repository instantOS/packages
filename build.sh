#!/bin/bash
set -e

# This script runs on the HOST (CI runner or local machine)
#
# Usage:
#   ./build.sh              build all packages (multi-pass dependency retry)
#   ./build.sh <pkg> ...    build only the given packages; each argument is
#                           either a package directory (packages/<name>) or
#                           an AUR package (aur/<name>)
#   ./build.sh --check      validate the signing configuration only

# --- Package signing configuration ------------------------------------------
# Signing is enabled when GPG_PRIVATE_KEY (base64'd armored export of the
# signing subkey) is provided, from CI secrets or the local environment.
# The matching public key material lives in packages/instantos-keyring/ and
# must be present whenever signing is enabled. See README.md.

KEYRING_DIR="packages/instantos-keyring"
if [ -s "$KEYRING_DIR/instantos.gpg" ] && [ -s "$KEYRING_DIR/instantos-trusted" ]; then
    has_keyring=true
else
    has_keyring=false
fi

if [ -n "${GPG_PRIVATE_KEY:-}" ] && ! $has_keyring; then
    echo "ERROR: GPG_PRIVATE_KEY is set but $KEYRING_DIR has no key material." >&2
    echo "Populate $KEYRING_DIR with the public key (README.md, 'Package signing')." >&2
    exit 1
fi

if [ -n "${GPG_PRIVATE_KEY:-}" ] && [ -z "${GPG_KEYID:-}" ]; then
    echo "ERROR: GPG_PRIVATE_KEY is set but GPG_KEYID (signing subkey id) is not." >&2
    exit 1
fi

# --- Dispatch by mode --------------------------------------------------------

mode=full
if [ "${1:-}" == "--check" ]; then
    echo "Signing configuration OK."
    exit 0
fi
if [ $# -gt 0 ]; then
    mode=single
fi

# Create repo directory.
# Signed full builds start from a clean repo: every package is rebuilt on each
# run anyway, and this keeps leftovers of previous runs out of the repository.
if [ "$mode" == "full" ] && [ -n "${GPG_PRIVATE_KEY:-}" ]; then
    rm -rf repo
fi
mkdir -p repo
# Ensure repo directory is writable by the container user (usually 1000:1000 or similar, but 777 is safest for ephemeral builds)
chmod 777 repo

# Create a temporary builder image to avoid re-downloading updates for every package
echo "Creating temporary builder image..."
docker build -t instantos-builder - <<EOF
FROM archlinux:base-devel
RUN pacman -Syu --noconfirm --needed git sudo
EOF

# Function to build a package using Docker
build_package_in_container() {
    local pkg_dir=$1
    echo "Building package in $pkg_dir using Docker"

    # Run the build in a container
    # We mount:
    # - The package directory to /pkg
    # - The repo directory to /repo
    # - The container_build.sh script to /build.sh
    # We pass HOST_UID and HOST_GID to fix permissions after build
    docker run --rm \
        -e HOST_UID="$(id -u)" \
        -e HOST_GID="$(id -g)" \
        -v "$(pwd)/$pkg_dir:/pkg" \
        -v "$(pwd)/repo:/repo" \
        -v "$(pwd)/container_build.sh:/build.sh" \
        instantos-builder \
        /bin/bash /build.sh
}

# List of packages to build
packages_to_build=()

# --- Single-package mode: build exactly what was asked for ------------------
# The CI matrix jobs use this; packages from previous build layers are
# downloaded into repo/ as plain package files, so create the database they
# are resolved through when it does not exist yet.
if [ "$mode" == "single" ]; then
    if [ ! -f repo/instant.db.tar.gz ] && ls repo/*.pkg.tar.zst >/dev/null 2>&1; then
        echo "Creating local repository database..."
        docker run --rm \
            -v "$(pwd)/repo:/repo" \
            instantos-builder \
            bash -c 'repo-add /repo/instant.db.tar.gz /repo/*.pkg.tar.zst'
    fi

    for pkg in "$@"; do
        # AUR packages are identified by their aur/<name> path
        if [[ $pkg == aur/* ]]; then
            aur_name=${pkg#aur/}
            mkdir -p aur_sources
            if [ ! -d "aur_sources/$aur_name" ]; then
                echo "Fetching AUR package: $aur_name"
                git clone "https://aur.archlinux.org/$aur_name.git" "aur_sources/$aur_name"
            fi
            pkg="aur_sources/$aur_name"
        fi
        build_package_in_container "$pkg"
    done

    # Signing happens after all package code has stopped, in a container that
    # can see the artifacts but cannot see the PKGBUILD or its build directory.
    if [ -n "${GPG_PRIVATE_KEY:-}" ]; then
        ./assemble.sh
    fi

    echo "Build complete!"
    exit 0
fi

# 1. Collect AUR packages first
echo "Collecting AUR packages..."
if [ -f aurpackages ]; then
    mkdir -p aur_sources

    while IFS= read -r line || [[ -n $line ]]; do
        if [[ -z $line ]] || [[ $line == \#* ]]; then continue; fi

        pkgname=$(echo "$line" | cut -d':' -f1)

        # Check if already cloned
        if [ ! -d "aur_sources/$pkgname" ]; then
            echo "Fetching AUR package: $pkgname"
            git clone "https://aur.archlinux.org/$pkgname.git" "aur_sources/$pkgname"
        fi

        if [ -d "aur_sources/$pkgname" ]; then
            packages_to_build+=("aur_sources/$pkgname")
        else
            echo "Failed to clone $pkgname"
        fi

    done <aurpackages
fi

# 2. Collect local packages second
echo "Collecting local packages..."
for d in packages/*/; do
    if [ "$d" == "repo/" ]; then continue; fi
    dirname=${d%/}
    if [[ $dirname == .* ]]; then continue; fi
    # Remove packages/ prefix to get the package name
    dirname=${dirname#packages/}

    if [ -f "packages/$dirname/PKGBUILD" ]; then
        # The keyring package only builds once real key material is committed
        if [ "$dirname" == "instantos-keyring" ] && ! $has_keyring; then
            echo "Skipping instantos-keyring (no key material yet, see README.md)"
            continue
        fi
        packages_to_build+=("packages/$dirname")
    fi
done

# 3. Build loop (Multi-pass)
echo "Starting build process for ${#packages_to_build[@]} packages..."

max_passes=10
pass=1

while [ ${#packages_to_build[@]} -gt 0 ]; do
    echo "=== Build Pass $pass ==="
    echo "Packages remaining: ${packages_to_build[*]}"

    failed_packages=()
    built_count=0

    for pkg in "${packages_to_build[@]}"; do
        # Try to build
        if build_package_in_container "$pkg"; then
            echo "Successfully built $pkg"
            built_count=$((built_count + 1))
        else
            echo "Failed to build $pkg (might be missing dependencies, will retry)"
            failed_packages+=("$pkg")
        fi
    done

    # Check progress
    if [ $built_count -eq 0 ]; then
        echo "ERROR: Could not build any of the remaining packages in this pass."
        echo "Remaining packages: ${failed_packages[*]}"
        echo "Possible causes: Circular dependencies, missing external dependencies, or build errors."
        exit 1
    fi

    # Prepare for next pass
    packages_to_build=("${failed_packages[@]}")
    pass=$((pass + 1))

    if [ $pass -gt $max_passes ]; then
        echo "ERROR: Reached maximum number of build passes ($max_passes)."
        exit 1
    fi
done

# Never expose the signing key to a package build container. The assembler is
# the only process that receives it and only the artifact directory is mounted.
if [ -n "${GPG_PRIVATE_KEY:-}" ]; then
    ./assemble.sh
fi

# Cleanup
rm -rf aur_sources

echo "Aggressive Docker cleanup..."
docker system prune -af

echo "Build complete!"
