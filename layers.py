#!/usr/bin/env python3
"""Compute dependency layers for the parallel CI build.

Every packages/*/ PKGBUILD and every AUR package from ./aurpackages is
inspected for depends/makedepends pointing at packages built by this
repository. Local PKGBUILDs are trusted repository code; mutable AUR metadata
is read from declarative .SRCINFO files and is never executed. Packages are
then grouped into layers: layer N contains the packages whose deepest internal
dependency lives in layer N-1, so all packages within a layer can be built
concurrently once all previous layers exist.

Writes level0..levelN as JSON arrays (GitHub Actions matrix "include"
format) to $GITHUB_OUTPUT when set, and a human-readable summary to
stdout. Fails loudly on dependency cycles and on chains deeper than the
number of build-N jobs in the workflow.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent

# build-0 .. build-4 jobs exist in .github/workflows/build.yml; extend the
# workflow (and this constant) if the dependency chain ever grows deeper.
MAX_LAYERS = 5

# GitHub's Arch Linux build runners are x86_64. Architecture-specific .SRCINFO
# fields for other targets must not affect this build graph.
TARGET_ARCH = "x86_64"

EXTRACT_SH = r"""
cd "$1"
source ./PKGBUILD
echo "PKGNAME $pkgname"
for x in "${depends[@]}"; do echo "DEP $x"; done
for x in "${makedepends[@]}"; do echo "MAKEDEP $x"; done
for x in "${provides[@]}"; do echo "PROVIDES $x"; done
"""


@dataclass
class PkgMeta:
    """Metadata for one source package build."""

    pkgname: str
    depends: set[str] = field(default_factory=set)
    makedepends: set[str] = field(default_factory=set)
    provides: set[str] = field(default_factory=set)


class DependencyCycleError(Exception):
    """Raised when the package graph cannot be topologically sorted."""

    def __init__(self, edges: dict[str, set[str]], stuck: set[str]):
        detail = "\n".join(
            f"  {n} depends on: {', '.join(sorted(edges[n]))}"
            for n in sorted(stuck)
        )
        super().__init__(f"dependency cycle among:\n{detail}")


class MatrixEntry(TypedDict):
    """One GitHub Actions matrix include entry."""

    name: str
    path: str
    level: int


def parse_extract_output(text: str) -> PkgMeta:
    """Parse EXTRACT_SH output into a PkgMeta."""
    pkgname: str | None = None
    depends: set[str] = set()
    makedepends: set[str] = set()
    provides: set[str] = set()
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        rest = rest.strip()
        if kind == "PKGNAME" and rest:
            pkgname = rest
        elif kind == "DEP" and rest:
            depends.add(rest)
        elif kind == "MAKEDEP" and rest:
            makedepends.add(rest)
        elif kind == "PROVIDES" and rest:
            provides.add(rest)
    if pkgname is None:
        raise ValueError("does not set pkgname")
    return PkgMeta(pkgname, depends, makedepends, provides)


def extract_meta(pkgdir: Path) -> PkgMeta:
    """Source a PKGBUILD in a bash subprocess and extract its metadata."""
    proc = subprocess.run(
        ["bash", "-c", EXTRACT_SH, "_", str(pkgdir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"ERROR: failed to source {pkgdir}/PKGBUILD:\n{proc.stderr}")
    try:
        return parse_extract_output(proc.stdout)
    except ValueError as exc:
        sys.exit(f"ERROR: {pkgdir}/PKGBUILD {exc}")


def _srcinfo_values(
    base: dict[str, list[str]], package: dict[str, list[str]], key: str
) -> set[str]:
    """Resolve generic and target-architecture values for one package."""
    resolved: set[str] = set()
    for field in (key, f"{key}_{TARGET_ARCH}"):
        inherited = base.get(field, [])
        local = package.get(field)
        if local is None:
            values = inherited
        elif local and local[0] == "":
            # An empty first assignment explicitly unsets inherited values.
            values = local[1:]
        else:
            values = [*inherited, *local]
        resolved.update(value for value in values if value)
    return resolved


def parse_srcinfo(text: str) -> PkgMeta:
    """Parse declarative AUR .SRCINFO metadata without executing package code."""
    base_name: str | None = None
    base: dict[str, list[str]] = {}
    packages: list[tuple[str, dict[str, list[str]]]] = []
    current: dict[str, list[str]] | None = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"line {lineno}: expected 'key = value'")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"line {lineno}: empty key")

        if key == "pkgbase":
            if base_name is not None or packages or not value:
                raise ValueError(f"line {lineno}: invalid pkgbase section")
            base_name = value
            current = base
        elif key == "pkgname":
            if base_name is None or not value:
                raise ValueError(f"line {lineno}: invalid pkgname section")
            fields: dict[str, list[str]] = {}
            packages.append((value, fields))
            current = fields
        elif current is None:
            raise ValueError(f"line {lineno}: metadata precedes pkgbase")
        else:
            current.setdefault(key, []).append(value)

    if base_name is None:
        raise ValueError("does not define pkgbase")
    if not packages:
        raise ValueError("does not define any pkgname sections")
    package_names = [name for name, _ in packages]
    if len(set(package_names)) != len(package_names):
        raise ValueError("defines duplicate pkgname sections")

    depends: set[str] = set()
    makedepends: set[str] = set()
    provides: set[str] = set(package_names[1:])
    for _, package in packages:
        depends.update(_srcinfo_values(base, package, "depends"))
        makedepends.update(_srcinfo_values(base, package, "makedepends"))
        provides.update(_srcinfo_values(base, package, "provides"))
    return PkgMeta(package_names[0], depends, makedepends, provides)


def extract_aur_meta(pkgdir: Path) -> PkgMeta:
    """Read an AUR package's required .SRCINFO file, failing closed."""
    srcinfo = pkgdir / ".SRCINFO"
    try:
        text = srcinfo.read_text()
    except OSError as exc:
        sys.exit(f"ERROR: cannot read AUR metadata {srcinfo}: {exc}")
    try:
        return parse_srcinfo(text)
    except ValueError as exc:
        sys.exit(f"ERROR: invalid AUR metadata {srcinfo}: {exc}")


def canon(dep: str) -> str:
    """Strip version constraints: 'foo>=1.2' -> 'foo'."""
    return re.split(r"[<>=]", dep)[0].strip()


def has_keyring_material(keyring_dir: Path) -> bool:
    """True when the instantos-keyring package is populated for real."""
    gpg = keyring_dir / "instantos.gpg"
    trusted = keyring_dir / "instantos-trusted"
    return (
        gpg.is_file()
        and gpg.stat().st_size > 0
        and trusted.is_file()
        and trusted.stat().st_size > 0
    )


def collect_nodes(root: Path | None = None) -> dict[str, PkgMeta]:
    """Return {node id: meta}; node ids are packages/<dir> or aur/<name>."""
    if root is None:
        root = ROOT
    nodes: dict[str, PkgMeta] = {}
    for entry in sorted((root / "packages").iterdir()):
        pkgdir = Path("packages") / entry.name
        if not (root / pkgdir / "PKGBUILD").is_file():
            continue
        if entry.name == "instantos-keyring" and not has_keyring_material(
            root / pkgdir
        ):
            print("skipping instantos-keyring (no key material yet)")
            continue
        nodes[str(pkgdir)] = extract_meta(root / pkgdir)

    aur_file = root / "aurpackages"
    if aur_file.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="aur-meta-"))
        try:
            for line in aur_file.read_text().splitlines():
                name = line.strip().split(":")[0]
                if not name or name.startswith("#"):
                    continue
                target = tmp / name
                if not target.is_dir():
                    subprocess.run(
                        [
                            "git",
                            "clone",
                            "-q",
                            "--depth",
                            "1",
                            f"https://aur.archlinux.org/{name}.git",
                            str(target),
                        ],
                        check=True,
                    )
                nodes[f"aur/{name}"] = extract_aur_meta(target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return nodes


def build_provided(nodes: dict[str, PkgMeta]) -> dict[str, str]:
    """Map every name this repository provides to the node building it."""
    provided: dict[str, str] = {}
    for node, meta in nodes.items():
        provided.setdefault(meta.pkgname, node)
        for p in meta.provides:
            provided.setdefault(canon(p), node)
    return provided


def build_edges(nodes: dict[str, PkgMeta]) -> dict[str, set[str]]:
    """Internal dependency edges: node -> set of nodes it depends on."""
    provided = build_provided(nodes)
    edges: dict[str, set[str]] = {node: set() for node in nodes}
    for node, meta in nodes.items():
        for dep in meta.depends | meta.makedepends:
            target = provided.get(canon(dep))
            if target is not None and target != node:
                edges[node].add(target)
    return edges


def compute_layers(
    nodes: dict[str, PkgMeta], edges: dict[str, set[str]]
) -> list[list[str]]:
    """Group node ids into Kahn layers by dependency depth."""
    if not nodes:
        return []
    indeg = {n: len(deps) for n, deps in edges.items()}
    dependents: dict[str, set[str]] = {n: set() for n in nodes}
    for a, bs in edges.items():
        for b in bs:
            dependents[b].add(a)

    level: dict[str, int] = {n: 0 for n in indeg if indeg[n] == 0}
    frontier = list(level)
    while frontier:
        nxt = []
        for b in frontier:
            for a in dependents[b]:
                indeg[a] -= 1
                if indeg[a] == 0:
                    level[a] = max(level[t] for t in edges[a]) + 1
                    nxt.append(a)
        frontier = nxt

    if len(level) != len(nodes):
        raise DependencyCycleError(edges, set(nodes) - set(level))

    layers: list[list[str]] = [[] for _ in range(max(level.values()) + 1)]
    for n, l in level.items():
        layers[l].append(n)
    for layer in layers:
        layer.sort()  # deterministic despite set iteration order
    return layers


def build_entries(
    layers: list[list[str]], nodes: dict[str, PkgMeta]
) -> list[list[MatrixEntry]]:
    """Turn layers into name-sorted GitHub Actions matrix include entries."""
    entries: list[list[MatrixEntry]] = []
    for i, layer in enumerate(layers):
        level_entries: list[MatrixEntry] = [
            {"name": nodes[n].pkgname, "path": n, "level": i} for n in layer
        ]
        level_entries.sort(key=lambda e: e["name"])
        entries.append(level_entries)
    return entries


def main() -> None:
    nodes = collect_nodes()
    if not nodes:
        sys.exit("ERROR: no packages found")

    edges = build_edges(nodes)
    try:
        layers = compute_layers(nodes, edges)
    except DependencyCycleError as exc:
        sys.exit(f"ERROR: {exc}")

    if len(layers) > MAX_LAYERS:
        sys.exit(
            f"ERROR: dependency chain is {len(layers)} levels deep, "
            f"workflow only has {MAX_LAYERS} build jobs; extend "
            f".github/workflows/build.yml and MAX_LAYERS in layers.py"
        )

    entries = build_entries(layers, nodes)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            for i in range(MAX_LAYERS):
                data = json.dumps(entries[i]) if i < len(entries) else "[]"
                f.write(f"level{i}={data}\n")

    print(f"{len(nodes)} packages, {len(layers)} layers:")
    for i, level_entries in enumerate(entries):
        names = ", ".join(e["name"] for e in level_entries)
        print(f"  layer {i} ({len(level_entries)}): {names}")


if __name__ == "__main__":
    main()
