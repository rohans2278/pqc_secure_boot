"""Tests for the deploy stage.

Covers the deterministic, host-side parts that CAN be proven without a Pi: the FIT
kernel-data offset parser (the security-relevant derived value), the rebranded
boot.txt renderer, the preconditions, and the SSH orchestration ORDER (with a fully
mocked ssh module). The live SSH+tryboot+reboot path itself is unverified without
real hardware.
"""

import types
from pathlib import Path

import pytest

from pqc_boot import ssh
from pqc_boot.config import VERIFIED_MARKER, Config
from pqc_boot.context import Context
from pqc_boot.stages import build, deploy, sign

POC_ITB = Path.home() / "fit-image" / "rpi5.itb"


def _ctx(tmp_path) -> Context:
    return Context.create(Config(workspace=Path(tmp_path), pi_ip="10.0.0.5"))


# --- derived value: the FIT kernel-data offset ---

def test_kernel_data_offset_matches_poc():
    if not POC_ITB.exists():
        pytest.skip("PoC rpi5.itb not present")
    assert deploy._kernel_data_offset(POC_ITB) == 0xf0  # => unzip src 0x300000f0


# --- boot.txt renderer (rebrand + derived values) ---

def test_render_boot_txt_has_marker_and_derived_values():
    txt = deploy.render_boot_txt(dtb_len=0x9fb8, unzip_src=0x300000f0,
                                 bootargs=f"root=PARTUUID=ab-02 rootwait {VERIFIED_MARKER}")
    assert VERIFIED_MARKER in txt
    assert "pqc-boot: ML-DSA-44 verification failed" in txt
    assert "cp.b 0x10000000 ${fdtcontroladdr} 0x9fb8" in txt
    assert "unzip 0x300000f0 0x80000" in txt


def test_render_boot_txt_has_no_quboot_tokens():
    txt = deploy.render_boot_txt(dtb_len=1, unzip_src=2, bootargs=VERIFIED_MARKER)
    assert "quboot" not in txt.lower()


# --- preconditions raise before any SSH ---

def test_run_without_build_artifacts_raises(tmp_path):
    with pytest.raises(RuntimeError, match="build stage first"):
        deploy.run(_ctx(tmp_path))


def test_run_without_pi_ip_raises(tmp_path):
    ctx = Context.create(Config(workspace=Path(tmp_path)))  # no pi_ip
    _stage_fake_artifacts(ctx)
    with pytest.raises(RuntimeError, match="no Pi IP"):
        deploy.run(ctx)


# --- SSH orchestration order (mocked ssh) ---

def test_deploy_orchestration_order(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _stage_fake_artifacts(ctx)

    calls: list[tuple] = []

    def rec(name):
        def f(conn, *a, **k):
            calls.append((name, *a))
            if name == "run_checked":
                cmd = a[0]
                out = ""
                if "cmdline.txt" in cmd:
                    out = "console=tty1 root=PARTUUID=ab-02 rootfstype=ext4 rootwait"
                elif "config.txt" in cmd:
                    out = "[all]\ndtparam=audio=on\n"
                return types.SimpleNamespace(stdout=out, stderr="", ok=True, exited=0)
            return types.SimpleNamespace(stdout="", stderr="", ok=True, exited=0)
        return f

    fake_conn = types.SimpleNamespace(close=lambda: calls.append(("close",)))
    monkeypatch.setattr(ssh, "connect", lambda ip, user: fake_conn)
    for n in ("sudo_checked", "run_checked", "run_remote", "push_root"):
        monkeypatch.setattr(ssh, n, rec(n))
    monkeypatch.setattr(deploy, "_kernel_data_offset", lambda p: 0xf0)
    # Stub the mkimage call (ctx.run) so boot.scr build "succeeds" without a real tool.
    monkeypatch.setattr(ctx, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stderr=""))

    deploy.run(ctx)

    names = [c[0] for c in calls]
    # Upfront passwordless-sudo gate is the first remote action.
    assert names[0] == "sudo_checked" and calls[0][1] == "true"
    # cmdline.txt fetched for bootargs.
    assert any(c[0] == "run_checked" and "cmdline.txt" in c[1] for c in calls)
    # The pubkey DTB is pushed AS u-boot.dtb, and all four artifacts + tryboot.txt go.
    pushed = {c[2] for c in calls if c[0] == "push_root"}
    assert f"{deploy.BOOT_DIR}/u-boot.dtb" in pushed
    assert f"{deploy.BOOT_DIR}/u-boot.bin" in pushed
    assert f"{deploy.BOOT_DIR}/boot.scr" in pushed
    assert f"{deploy.BOOT_DIR}/{sign.ITB_NAME}" in pushed
    assert f"{deploy.BOOT_DIR}/tryboot.txt" in pushed
    # The one-shot tryboot reboot is issued.
    assert any(c[0] == "run_remote" and 'reboot "0 tryboot"' in c[1] for c in calls)


def _stage_fake_artifacts(ctx: Context) -> None:
    """Create the on-disk artifacts deploy's preconditions check for."""
    tools = ctx.uboot_dir / "tools"
    tools.mkdir(parents=True)
    (tools / "mkimage").write_text("")
    (ctx.uboot_dir / "u-boot.bin").write_text("")
    (ctx.uboot_dir / build.PUBKEY_DTB).write_text("dtbdtb")
    fdir = sign.fit_dir(ctx)
    fdir.mkdir(parents=True)
    (fdir / sign.ITB_NAME).write_text("")
