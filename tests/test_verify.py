import types
from pathlib import Path

import pytest

from pqc_boot import ssh
from pqc_boot.config import VERIFIED_MARKER, Config
from pqc_boot.context import Context
from pqc_boot.stages import verify
from pqc_boot.stages.verify import cmdline_is_verified


def _ctx(tmp_path, ip="10.0.0.5") -> Context:
    return Context.create(Config(workspace=Path(tmp_path), pi_ip=ip))


def test_marker_present():
    cmdline = f"console=ttyAMA10,115200 root=PARTUUID=29e21ec0-02 rootwait {VERIFIED_MARKER}"
    assert cmdline_is_verified(cmdline)


def test_marker_absent():
    assert not cmdline_is_verified("console=ttyAMA10,115200 root=/dev/mmcblk0p2 rootwait")


def test_marker_must_be_a_whole_token():
    # a substring match must not count as verified
    assert not cmdline_is_verified("xpqc-boot_verified=10")


def test_empty_cmdline_is_not_verified():
    # (a failed READ is handled upstream as SSHCommandError, not passed here as "")
    assert not cmdline_is_verified("")


def test_marker_value_is_pinned():
    assert VERIFIED_MARKER == "pqc-boot_verified=1"


def test_run_without_pi_ip_raises(tmp_path):
    ctx = Context.create(Config(workspace=Path(tmp_path)))
    with pytest.raises(RuntimeError, match="requires the Pi IP"):
        verify.run(ctx)


def test_run_success_promotes_atomically(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    sudo: list[str] = []
    monkeypatch.setattr(verify, "_wait_for_pi",
                        lambda c: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ssh, "read_cmdline", lambda c: f"root=ab {VERIFIED_MARKER}")
    monkeypatch.setattr(ssh, "sudo_checked",
                        lambda c, cmd, **k: sudo.append(cmd) or types.SimpleNamespace(ok=True))

    verify.run(ctx)

    promote = [c for c in sudo if "tryboot.txt" in c and "config.txt" in c]
    assert promote, "expected a promote (tryboot.txt -> config.txt)"
    # atomic: stage to .new then mv into place
    assert "config.txt.new" in promote[0] and "mv" in promote[0]


def test_run_not_verified_does_not_promote(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    sudo: list[str] = []
    monkeypatch.setattr(verify, "_wait_for_pi",
                        lambda c: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ssh, "read_cmdline", lambda c: "root=ab rootwait")  # no marker
    monkeypatch.setattr(ssh, "sudo_checked", lambda c, cmd, **k: sudo.append(cmd))

    with pytest.raises(RuntimeError, match="verification failed"):
        verify.run(ctx)
    assert sudo == [], "must not promote when the marker is absent"


def test_wait_for_pi_times_out(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(verify, "RECONNECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(verify, "RECONNECT_POLL_S", 0.0)
    monkeypatch.setattr(ssh, "connect",
                        lambda ip, user: types.SimpleNamespace(close=lambda: None))

    def _refused(conn, cmd):
        raise OSError("connection refused")

    monkeypatch.setattr(ssh, "run_remote", _refused)
    with pytest.raises(RuntimeError, match="did not return within"):
        verify._wait_for_pi(ctx)
