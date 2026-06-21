"""Tests for the generate-patch maintainer tool.

The clone + Claude + cross-build paths are external; these tests cover the parts that
must be correct offline: deterministic assembly of the 40 added files, the per-edit
ALLOWLIST screen, and that the stored wrapper assets stay byte-identical to the pinned
patch (drift guard).
"""

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from pqc_boot import ssh  # noqa: F401  (ensure package import graph is fine)
from pqc_boot.ai import build_fixer, patch_generator as pg
from pqc_boot.config import Config
from pqc_boot.context import Context

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git required")


def _ctx(tmp_path) -> Context:
    return Context.create(Config(workspace=Path(tmp_path)))


def _extract_added_from_patch(path: str) -> bytes:
    """Re-derive an added file's body from the pinned unified diff (independent of the
    tool's own extractor) for the byte-identity guard."""
    lines = pg.PINNED_PATCH.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if lines[i].startswith("diff --git ") and lines[i].split(" b/", 1)[1].strip() == path:
            body, in_hunk, j = [], False, i + 1
            while j < len(lines) and not lines[j].startswith("diff --git "):
                l = lines[j]
                if l.startswith("@@"):
                    in_hunk = True
                elif in_hunk and l.startswith("+"):
                    body.append(l[1:])
                elif in_hunk and l.startswith("\\ No newline") and body:
                    body[-1] = body[-1].rstrip("\n")
                j += 1
            return "".join(body).encode()
        i += 1
    raise AssertionError(f"{path} not found in pinned patch")


def test_assembly_produces_exactly_40_added_files(tmp_path):
    tree = tmp_path / "u-boot"
    pg.vendor_core(tree)
    pg.write_wrappers(tree)

    files = [p for p in tree.rglob("*") if p.is_file()]
    assert len(files) == 40, sorted(str(f.relative_to(tree)) for f in files)
    # core present + byte-identical; keygen excluded; wrappers present + byte-identical
    assert (tree / "lib/ml-dsa/mldsa_native.c").read_bytes() == \
        (pg.MLDSA_CORE_DIR / "mldsa_native.c").read_bytes()
    assert not (tree / "lib/ml-dsa/keygen.c").exists()
    assert (tree / "lib/ml-dsa/mldsa-verify.c").read_bytes() == \
        (pg.WRAPPER_ASSETS_DIR / "lib/ml-dsa/mldsa-verify.c").read_bytes()
    assert (tree / "include/u-boot/ml-dsa.h").is_file()


def test_wrapper_assets_match_pinned_patch():
    for rel in ("include/u-boot/ml-dsa.h", "lib/ml-dsa/mldsa-verify.c",
                "lib/ml-dsa/mldsa-sign.c", "lib/ml-dsa/Kconfig", "lib/ml-dsa/Makefile"):
        asset = (pg.WRAPPER_ASSETS_DIR / rel).read_bytes()
        assert asset == _extract_added_from_patch(rel), f"{rel} drifted from the pinned patch"


def test_expected_edits_are_the_six_modified_files():
    assert set(pg.EXPECTED_EDITS) == {
        "boot/image-sig.c", "tools/image-sig-host.c", "lib/Kconfig",
        "lib/Makefile", "tools/Makefile", "configs/rpi_arm64_defconfig",
    }


@needs_git
def test_apply_edit_rejects_allowlist_violation(tmp_path, monkeypatch):
    tree = tmp_path / "u-boot"
    (tree / "boot").mkdir(parents=True)
    (tree / "boot/image-sig.c").write_text("#include <u-boot/rsa.h>\n")
    subprocess.run([GIT, "init", "-q", str(tree)], check=True)

    monkeypatch.setattr(pg, "request_edit", lambda *a, **k: "DIFF")
    # Claude's diff (claims to) touch an extra file -> allowlist violation, no apply.
    monkeypatch.setattr(build_fixer, "files_changed_by_apply",
                        lambda diff, repo, **k: ["boot/image-sig.c", "evil.c"])
    with pytest.raises(RuntimeError, match="allowlist"):
        pg._apply_edit(_ctx(tmp_path), tree, "boot/image-sig.c", "add include")


@needs_git
def test_apply_edit_applies_single_file_diff(tmp_path, monkeypatch):
    tree = tmp_path / "u-boot"
    (tree / "boot").mkdir(parents=True)
    f = tree / "boot/image-sig.c"
    f.write_text("#include <u-boot/rsa.h>\nint x;\n")
    subprocess.run([GIT, "init", "-q", str(tree)], check=True)
    subprocess.run([GIT, "-C", str(tree), "add", "-A"], check=True)
    subprocess.run([GIT, "-C", str(tree), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)

    # Produce a valid 1-file diff via git itself, then revert the worktree.
    f.write_text("#include <u-boot/rsa.h>\n#include <u-boot/ml-dsa.h>\nint x;\n")
    diff = subprocess.run([GIT, "-C", str(tree), "diff"], capture_output=True, text=True).stdout
    subprocess.run([GIT, "-C", str(tree), "checkout", "--", "boot/image-sig.c"], check=True)

    monkeypatch.setattr(pg, "request_edit", lambda *a, **k: diff)
    pg._apply_edit(_ctx(tmp_path), tree, "boot/image-sig.c", "add include")
    assert "u-boot/ml-dsa.h" in f.read_text()


def test_dry_run_writes_nothing(tmp_path):
    ctx = Context.create(Config(workspace=Path(tmp_path)), dry_run=True)
    before = pg.PINNED_PATCH.read_bytes()
    pg.run(ctx)
    assert pg.PINNED_PATCH.read_bytes() == before          # pinned patch untouched
    assert not (Path(tmp_path) / "patchgen").exists()       # no clone happened
