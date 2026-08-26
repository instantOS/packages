#!/bin/bash
set -e

# Static checks used by CI (lint job in .github/workflows/build.yml) and by
# `just check`: shell formatting (shfmt), type checking (ty) and unit tests
# (pytest).

echo "=== shfmt ==="
find . -type f -name "*.sh" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +
find packages/ -type f -name "PKGBUILD" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +
find . -type f -name "*.install" -not -path "*/.*" -exec shfmt -d -s -i 4 {} +

echo "=== ty ==="
uvx ty check

echo "=== pytest ==="
uvx pytest

echo "All checks passed."
