# Format all shell scripts and PKGBUILDs
fmt:
    @echo "Formatting shell scripts..."
    find . -type f -name "*.sh" -not -path "*/.*" -exec shfmt -w -s -i 4 {} +
    @echo "Formatting PKGBUILDs..."
    find packages/ -type f -name "PKGBUILD" -not -path "*/.*" -exec shfmt -w -s -i 4 {} +
    @echo "Formatting install files..."
    find . -type f -name "*.install" -not -path "*/.*" -exec shfmt -w -s -i 4 {} +
    @echo "Done!"

# Check formatting without modifying files
check-fmt:
    @echo "Checking formatting..."
    find . -type f -name "*.sh" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +
    find packages/ -type f -name "PKGBUILD" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +
    find . -type f -name "*.install" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +

fetch-pkgbuilds:
    cp ~/workspace/instantWM/packaging/arch/PKGBUILD ./packages/instantwm/
