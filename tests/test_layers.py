"""Unit tests for layers.py.

Everything here runs without network access; AUR cloning inside
collect_nodes is not exercised (no aurpackages file in the fixtures).
"""

import pytest

import layers
from layers import PkgMeta


# --- canon --------------------------------------------------------------------


def test_canon_strips_version_constraints():
    assert layers.canon("foo") == "foo"
    assert layers.canon("foo>=1.2") == "foo"
    assert layers.canon("foo<2") == "foo"
    assert layers.canon("foo=1:2.0-1") == "foo"


# --- metadata extraction ------------------------------------------------------


def test_parse_extract_output():
    text = (
        "PKGNAME hello\n"
        "DEP bash\n"
        "DEP bar>=2.0\n"
        "MAKEDEP git\n"
        "PROVIDES hello-world\n"
    )
    assert layers.parse_extract_output(text) == PkgMeta(
        pkgname="hello",
        depends={"bash", "bar>=2.0"},
        makedepends={"git"},
        provides={"hello-world"},
    )


def test_parse_extract_output_requires_pkgname():
    with pytest.raises(ValueError):
        layers.parse_extract_output("DEP bash\n")


def test_parse_extract_output_ignores_empty_fields():
    meta = layers.parse_extract_output("PKGNAME solo\nDEP \nMAKEDEP\n")
    assert meta == PkgMeta(pkgname="solo")


def test_extract_meta_sources_a_real_pkgbuild(tmp_path):
    pkgdir = tmp_path / "hello"
    pkgdir.mkdir()
    (pkgdir / "PKGBUILD").write_text(
        "pkgname=hello\n"
        "pkgver=1.0\n"
        "depends=('glibc' 'bar>=2.0')\n"
        "makedepends=('git')\n"
        "provides=('hello-world')\n"
    )
    assert layers.extract_meta(pkgdir) == PkgMeta(
        pkgname="hello",
        depends={"glibc", "bar>=2.0"},
        makedepends={"git"},
        provides={"hello-world"},
    )


def test_parse_srcinfo_handles_split_packages_and_target_architecture():
    text = """
pkgbase = example
  depends = common
  depends_x86_64 = x86-runtime
  depends_aarch64 = arm-runtime
  makedepends = cmake
  makedepends_x86_64 = rust
  provides = base-virtual

pkgname = example
  depends = cli-runtime
  provides = example-cli

pkgname = example-docs
  depends = docs-runtime
  provides_x86_64 = example-help
"""
    assert layers.parse_srcinfo(text) == PkgMeta(
        pkgname="example",
        depends={"common", "x86-runtime", "cli-runtime", "docs-runtime"},
        makedepends={"cmake", "rust"},
        provides={
            "base-virtual",
            "example-cli",
            "example-docs",
            "example-help",
        },
    )


def test_parse_srcinfo_honors_empty_override():
    text = """
pkgbase = example
  depends = inherited
pkgname = example
  depends =
  depends = replacement
"""
    assert layers.parse_srcinfo(text) == PkgMeta(
        pkgname="example", depends={"replacement"}
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("pkgname = nope\n", "invalid pkgname section"),
        ("pkgbase = nope\n", "does not define any pkgname"),
        ("this is not metadata\n", "expected 'key = value'"),
    ],
)
def test_parse_srcinfo_rejects_malformed_metadata(text, message):
    with pytest.raises(ValueError, match=message):
        layers.parse_srcinfo(text)


# --- graph construction and layering ------------------------------------------


def sample_nodes():
    return {
        "packages/a": PkgMeta("a"),
        "packages/b": PkgMeta("b", depends={"a"}),
        "packages/c": PkgMeta("c", makedepends={"a"}),
        "packages/d": PkgMeta("d", depends={"b", "c"}),
    }


def test_build_edges_uses_depends_and_makedepends():
    assert layers.build_edges(sample_nodes()) == {
        "packages/a": set(),
        "packages/b": {"packages/a"},
        "packages/c": {"packages/a"},
        "packages/d": {"packages/b", "packages/c"},
    }


def test_build_edges_resolves_provides():
    nodes = {
        "packages/a": PkgMeta("a", provides={"shiny"}),
        "packages/b": PkgMeta("b", depends={"shiny>=2"}),
    }
    assert layers.build_edges(nodes) == {
        "packages/a": set(),
        "packages/b": {"packages/a"},
    }


def test_build_edges_ignores_external_and_self_dependencies():
    nodes = {"packages/a": PkgMeta("a", depends={"bash", "a", "python"})}
    assert layers.build_edges(nodes) == {"packages/a": set()}


def test_compute_layers_groups_by_depth():
    result = layers.compute_layers(sample_nodes(), layers.build_edges(sample_nodes()))
    assert result == [
        ["packages/a"],
        ["packages/b", "packages/c"],
        ["packages/d"],
    ]


def test_compute_layers_empty():
    assert layers.compute_layers({}, {}) == []


def test_compute_layers_detects_cycles():
    nodes = {
        "packages/a": PkgMeta("a", depends={"b"}),
        "packages/b": PkgMeta("b", depends={"a"}),
        "packages/c": PkgMeta("c"),
    }
    with pytest.raises(layers.DependencyCycleError) as excinfo:
        layers.compute_layers(nodes, layers.build_edges(nodes))
    assert "packages/a" in str(excinfo.value)
    assert "packages/b" in str(excinfo.value)


def test_build_entries_sorts_by_package_name():
    nodes = {
        "packages/zeta": PkgMeta("zeta"),
        "packages/yao": PkgMeta("yao"),
        "packages/alpha": PkgMeta("alpha", depends={"zeta", "yao"}),
    }
    entries = layers.build_entries(
        [["packages/zeta", "packages/yao"], ["packages/alpha"]], nodes
    )
    assert entries == [
        [
            {"name": "yao", "path": "packages/yao", "level": 0},
            {"name": "zeta", "path": "packages/zeta", "level": 0},
        ],
        [{"name": "alpha", "path": "packages/alpha", "level": 1}],
    ]


# --- repository collection ------------------------------------------------------


def make_pkg(root, name, content=None):
    pkgdir = root / "packages" / name
    pkgdir.mkdir(parents=True)
    (pkgdir / "PKGBUILD").write_text(content or f"pkgname={name}\n")
    return pkgdir


def test_collect_nodes_reads_local_packages(tmp_path):
    make_pkg(tmp_path, "foo", "pkgname=foo\ndepends=('bar')\n")
    make_pkg(tmp_path, "bar")
    (tmp_path / "packages" / "notapackage").mkdir()  # no PKGBUILD inside
    nodes = layers.collect_nodes(root=tmp_path)
    assert set(nodes) == {"packages/bar", "packages/foo"}
    assert nodes["packages/foo"] == PkgMeta("foo", depends={"bar"})


def test_collect_nodes_skips_placeholder_keyring(tmp_path):
    keyring = make_pkg(tmp_path, "instantos-keyring")
    (keyring / "instantos.gpg").touch()
    (keyring / "instantos-trusted").touch()
    make_pkg(tmp_path, "other")
    assert set(layers.collect_nodes(root=tmp_path)) == {"packages/other"}


def test_collect_nodes_includes_populated_keyring(tmp_path):
    keyring = make_pkg(tmp_path, "instantos-keyring")
    (keyring / "instantos.gpg").write_text("dummy")
    (keyring / "instantos-trusted").write_text("dummy")
    make_pkg(tmp_path, "other")
    assert set(layers.collect_nodes(root=tmp_path)) == {
        "packages/instantos-keyring",
        "packages/other",
    }


def test_collect_nodes_parses_aur_srcinfo_without_executing_pkgbuild(
    monkeypatch, tmp_path
):
    (tmp_path / "packages").mkdir()
    (tmp_path / "aurpackages").write_text("hostile\n")
    marker = tmp_path / "executed"

    def fake_run(args, **kwargs):
        assert args[:2] == ["git", "clone"]
        target = layers.Path(args[-1])
        target.mkdir()
        (target / "PKGBUILD").write_text(f"touch {marker}\n")
        (target / ".SRCINFO").write_text(
            "pkgbase = hostile\n"
            "  depends = local-dependency\n"
            "pkgname = hostile\n"
        )

    monkeypatch.setattr(layers.subprocess, "run", fake_run)

    assert layers.collect_nodes(root=tmp_path) == {
        "aur/hostile": PkgMeta("hostile", depends={"local-dependency"})
    }
    assert not marker.exists()


def test_extract_aur_meta_fails_closed_without_srcinfo(tmp_path):
    pkgdir = tmp_path / "aur-package"
    pkgdir.mkdir()
    (pkgdir / "PKGBUILD").write_text("pkgname=aur-package\n")

    with pytest.raises(SystemExit, match="cannot read AUR metadata"):
        layers.extract_aur_meta(pkgdir)


# --- main ------------------------------------------------------------------------


def test_main_writes_matrix_output(monkeypatch, tmp_path):
    nodes = {
        "packages/a": PkgMeta("a"),
        "packages/b": PkgMeta("b", depends={"a"}),
    }
    monkeypatch.setattr(layers, "collect_nodes", lambda root=None: nodes)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    layers.main()

    lines = out.read_text().splitlines()
    assert lines[0] == 'level0=[{"name": "a", "path": "packages/a", "level": 0}]'
    assert lines[1] == 'level1=[{"name": "b", "path": "packages/b", "level": 1}]'
    assert lines[2:] == ["level2=[]", "level3=[]", "level4=[]"]


def test_main_rejects_chains_deeper_than_max_layers(monkeypatch):
    nodes = {
        f"packages/p{i}": PkgMeta(f"p{i}", depends={f"p{i - 1}"} if i else set())
        for i in range(layers.MAX_LAYERS + 1)
    }
    monkeypatch.setattr(layers, "collect_nodes", lambda root=None: nodes)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        layers.main()
    assert "levels deep" in str(excinfo.value)
