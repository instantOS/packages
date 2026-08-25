#!/usr/bin/env python3
"""Compute dependency layers for the parallel CI build.

Every packages/*/ PKGBUILD and every AUR package from ./aurpackages is
inspected for depends/makedepends pointing at packages built by this
repository. Packages are then grouped into layers: layer N contains the
packages whose deepest internal dependency lives in layer N-1, so all
packages within a layer can be built concurrently once all previous
layers exist.

Writes level0..levelN as JSON arrays (GitHub Actions matrix "include"
format) to $GITHUB_OUTPUT when set, and a human-readable summary to
stdout. Fails loudly on dependency cycles and on chains deeper than the
number of build-N jobs in the workflow.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))

# build-0 .. build-4 jobs exist in .github/workflows/build.yml; extend the
# workflow (and this constant) if the dependency chain ever grows deeper.
MAX_LAYERS = 5

EXTRACT_SH = r"""
cd "$1"
source ./PKGBUILD
echo "PKGNAME $pkgname"
for x in "${depends[@]}"; do echo "DEP $x"; done
for x in "${makedepends[@]}"; do echo "MAKEDEP $x"; done
for x in "${provides[@]}"; do echo "PROVIDES $x"; done
"""


def extract_meta(pkgdir):
    proc = subprocess.run(
        ["bash", "-c", EXTRACT_SH, "_", pkgdir],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"ERROR: failed to source {pkgdir}/PKGBUILD:\n{proc.stderr}")
    meta = {"pkgname": None, "DEP": set(), "MAKEDEP": set(), "PROVIDES": set()}
    for line in proc.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        rest = rest.strip()
        if kind == "PKGNAME":
            meta["pkgname"] = rest
        elif kind in ("DEP", "MAKEDEP", "PROVIDES") and rest:
            meta[kind].add(rest)
    if not meta["pkgname"]:
        sys.exit(f"ERROR: {pkgdir}/PKGBUILD does not set pkgname")
    return meta


def canon(dep):
    """Strip version constraints: 'foo>=1.2' -> 'foo'."""
    return re.split(r"[<>=]", dep)[0].strip()


def collect_nodes():
    """Return {node id: meta}; node ids are packages/<dir> or aur/<name>."""
    nodes = {}
    for entry in sorted(os.listdir(os.path.join(ROOT, "packages"))):
        pkgdir = os.path.join("packages", entry)
        if not os.path.isfile(os.path.join(ROOT, pkgdir, "PKGBUILD")):
            continue
        if entry == "instantos-keyring":
            gpg = os.path.join(ROOT, pkgdir, "instantos.gpg")
            trusted = os.path.join(ROOT, pkgdir, "instantos-trusted")
            if not (
                os.path.isfile(gpg)
                and os.path.getsize(gpg) > 0
                and os.path.isfile(trusted)
                and os.path.getsize(trusted) > 0
            ):
                print("skipping instantos-keyring (no key material yet)")
                continue
        nodes[pkgdir] = extract_meta(os.path.join(ROOT, pkgdir))

    aur_file = os.path.join(ROOT, "aurpackages")
    if os.path.isfile(aur_file):
        tmp = tempfile.mkdtemp(prefix="aur-meta-")
        try:
            for line in open(aur_file):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name = line.split(":")[0]
                target = os.path.join(tmp, name)
                if not os.path.isdir(target):
                    subprocess.run(
                        [
                            "git",
                            "clone",
                            "-q",
                            "--depth",
                            "1",
                            f"https://aur.archlinux.org/{name}.git",
                            target,
                        ],
                        check=True,
                    )
                nodes[f"aur/{name}"] = extract_meta(target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return nodes


def main():
    nodes = collect_nodes()
    if not nodes:
        sys.exit("ERROR: no packages found")

    # map every name this repository provides to the node building it
    provided = {}
    for node, meta in nodes.items():
        provided.setdefault(meta["pkgname"], node)
        for p in meta["PROVIDES"]:
            provided.setdefault(canon(p), node)

    edges = defaultdict(set)
    for node, meta in nodes.items():
        for dep in meta["DEP"] | meta["MAKEDEP"]:
            target = provided.get(canon(dep))
            if target and target != node:
                edges[node].add(target)

    # Kahn's algorithm with level assignment
    indeg = {n: len(edges[n]) for n in nodes}
    dependents = defaultdict(set)
    for a, bs in edges.items():
        for b in bs:
            dependents[b].add(a)

    level = {n: 0 for n in nodes if indeg[n] == 0}
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
        stuck = sorted(set(nodes) - set(level))
        detail = "\n".join(
            f"  {n} depends on: {', '.join(sorted(edges[n]))}" for n in stuck
        )
        sys.exit(f"ERROR: dependency cycle among:\n{detail}")

    max_level = max(level.values())
    if max_level >= MAX_LAYERS:
        sys.exit(
            f"ERROR: dependency chain is {max_level + 1} levels deep, "
            f"workflow only has {MAX_LAYERS} build jobs; extend "
            f".github/workflows/build.yml and MAX_LAYERS in layers.py"
        )

    levels = [[] for _ in range(max_level + 1)]
    for n, l in level.items():
        levels[l].append({"name": nodes[n]["pkgname"], "path": n, "level": l})
    for arr in levels:
        arr.sort(key=lambda e: e["name"])

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            for i in range(MAX_LAYERS):
                data = json.dumps(levels[i]) if i < len(levels) else []
                f.write(f"level{i}={data}\n")

    print(f"{len(nodes)} packages, {max_level + 1} layers:")
    for i, arr in enumerate(levels):
        names = ", ".join(e["name"] for e in arr)
        print(f"  layer {i} ({len(arr)}): {names}")


if __name__ == "__main__":
    main()
