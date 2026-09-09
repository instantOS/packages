#!/bin/bash
set -e

# This script runs on the HOST

# Create repo directory
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

# 1. Collect AUR packages first
echo "Collecting AUR packages..."
if [ -f aurpackages ]; then
    mkdir -p aur_sources

    while IFS= read -r line || [[ -n $line ]]; do
        # Strip comments (full-line or inline `pkg # reason`) and trim whitespace
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        if [[ -z $line ]]; then continue; fi

        pkgname="$line"

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

# Cleanup
rm -rf aur_sources

echo "Aggressive Docker cleanup..."
docker system prune -af

echo "Build complete!"
