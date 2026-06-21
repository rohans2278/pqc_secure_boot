"""Tests for the build stage.

The fast tests check the plan text and that each precondition (clone / patch / keys)
raises a clear error before any make is invoked. The full two-pass cross-compile is
slow (minutes), so it is opt-in: set PQCBOOT_SLOW=1 and have a patched workspace
(.pqcboot-work) with keys present. It asserts the real artifacts land and that the
embedded pubkey node matches the proven PoC (algo + required + 1312 B, enforced
inside build.run via _assert_pubkey_node).
"""

import os
from pathlib import Path

import pytest

from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot.stages import build

REPO = Path(__file__).resolve().parents[1]
WS = REPO / ".pqcboot-work"

SLOW = os.environ.get("PQCBOOT_SLOW") == "1"
needs_slow = pytest.mark.skipif(
    not SLOW, reason="set PQCBOOT_SLOW=1 to run the full cross-compile"
)


def _ctx(tmp_path) -> Context:
    return Context.create(Config(workspace=Path(tmp_path)))


def test_plan_mentions_defconfig_and_dtb(tmp_path):
    p = build.plan(_ctx(tmp_path))
    assert "rpi_arm64_defconfig" in p and build.PUBKEY_DTB in p


def test_run_without_clone_raises(tmp_path):
    with pytest.raises(RuntimeError, match="clone stage first"):
        build.run(_ctx(tmp_path))


def test_run_unpatched_raises(tmp_path):
    (tmp_path / "u-boot" / ".git").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="not patched"):
        build.run(_ctx(tmp_path))


def test_run_without_keys_raises(tmp_path):
    uboot = tmp_path / "u-boot"
    (uboot / ".git").mkdir(parents=True)
    sentinel = uboot / build._PATCH_SENTINEL
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("// stub\n")
    with pytest.raises(RuntimeError, match="public key not found"):
        build.run(_ctx(tmp_path))


@needs_slow
def test_real_two_pass_build_embeds_pubkey():
    ctx = Context.create(Config(workspace=WS))
    if not (ctx.uboot_dir / build._PATCH_SENTINEL).exists():
        pytest.skip("workspace not patched; run patch + keys first")
    if not (ctx.keydir / f"{ctx.config.keyname}.pub").exists():
        pytest.skip("workspace has no keypair; run keys first")

    build.run(ctx)  # algo/required/1312 B assertion is enforced inside run()

    assert (ctx.uboot_dir / "u-boot.bin").is_file()
    dtb = ctx.uboot_dir / build.PUBKEY_DTB
    assert dtb.is_file() and dtb.stat().st_size > 0
