"""Tests for the sign stage.

The fast tests check the tool-owned .its generator and that each precondition
(build tools / control DTB / key / Pi IP) raises before any SSH. The slow test
(opt-in via PQCBOOT_SLOW=1) proves the real signing path: it copies the PoC FIT
inputs into a tmp dir (read-only reference -> copied out, never a runtime dep),
generates the .its, signs with the BUILT mkimage using only `-k` (no `-K`, since
build already embedded the pubkey), and confirms fit_check_sign verifies the
signature against the build's control DTB. No Pi is needed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot.stages import build, sign

REPO = Path(__file__).resolve().parents[1]
WS = REPO / ".pqcboot-work"
POC_FIT = Path.home() / "fit-image"

SLOW = os.environ.get("PQCBOOT_SLOW") == "1"
needs_slow = pytest.mark.skipif(
    not SLOW, reason="set PQCBOOT_SLOW=1 to run the full sign+verify"
)


def _ctx(tmp_path) -> Context:
    return Context.create(Config(workspace=Path(tmp_path)))


def test_its_generator_has_signature_node(tmp_path):
    its = sign.its_text("mykey")
    assert 'algo = "sha256,mldsa44"' in its
    assert 'key-name-hint = "mykey"' in its
    assert 'sign-images = "kernel", "fdt", "ramdisk"' in its
    assert 'compression = "gzip"' in its  # Pi 5 kernel is gzipped
    for incbin in (sign.KERNEL, sign.FDT, sign.RAMDISK):
        assert f'/incbin/("{incbin}")' in its


def test_its_keyname_is_parameterized(tmp_path):
    assert 'key-name-hint = "otherkey"' in sign.its_text("otherkey")


def test_run_without_build_tools_raises(tmp_path):
    with pytest.raises(RuntimeError, match="run the build stage first"):
        sign.run(_ctx(tmp_path))


def test_run_without_key_raises(tmp_path):
    # Satisfy the build-tool/DTB preconditions with stand-ins, omit the key.
    ctx = _ctx(tmp_path)
    tools = ctx.uboot_dir / "tools"
    tools.mkdir(parents=True)
    (tools / "mkimage").write_text("")
    (tools / "fit_check_sign").write_text("")
    (ctx.uboot_dir / build.PUBKEY_DTB).write_text("")
    with pytest.raises(RuntimeError, match="private key"):
        sign.run(ctx)


def test_run_without_pi_ip_raises(tmp_path):
    ctx = _ctx(tmp_path)
    tools = ctx.uboot_dir / "tools"
    tools.mkdir(parents=True)
    (tools / "mkimage").write_text("")
    (tools / "fit_check_sign").write_text("")
    (ctx.uboot_dir / build.PUBKEY_DTB).write_text("")
    ctx.keydir.mkdir(parents=True)
    (ctx.keydir / f"{ctx.config.keyname}.bin").write_text("")
    with pytest.raises(RuntimeError, match="no Pi IP"):
        sign.run(ctx)


@needs_slow
def test_real_sign_and_fit_check_sign():
    """Sign with the built mkimage (-k only) and verify with fit_check_sign."""
    uboot = WS / "u-boot"
    mkimage = uboot / "tools" / "mkimage"
    fit_check_sign = uboot / "tools" / "fit_check_sign"
    control_dtb = uboot / build.PUBKEY_DTB
    priv = WS / "keys" / "mykey.bin"
    for need in (mkimage, fit_check_sign, control_dtb, priv):
        if not need.exists():
            pytest.skip(f"missing {need}; run keys + build first")
    if not all((POC_FIT / n).exists() for n in sign.FIT_INPUTS):
        pytest.skip("PoC FIT inputs not present on this host")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fdir = Path(td)
        for n in sign.FIT_INPUTS:               # copy out of the read-only reference
            shutil.copy2(POC_FIT / n, fdir / n)
        (fdir / sign.ITS_NAME).write_text(sign.its_text("mykey"))

        # Sign with -k only (no -K): build already embedded the pubkey.
        r = subprocess.run(
            [str(mkimage), "-f", sign.ITS_NAME, "-k", str(WS / "keys"), sign.ITB_NAME],
            cwd=fdir, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        itb = fdir / sign.ITB_NAME
        assert itb.is_file() and itb.stat().st_size > 0

        # Verify the signature against the build's control DTB.
        v = subprocess.run(
            [str(fit_check_sign), "-f", str(itb), "-k", str(control_dtb)],
            capture_output=True, text=True,
        )
        assert v.returncode == 0, (v.stdout + v.stderr)
