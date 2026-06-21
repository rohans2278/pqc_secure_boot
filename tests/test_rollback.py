"""Tests for the rollback recovery command (SSH fully mocked — no Pi).

Proves the restore sequence: require a backup, restore config.txt atomically, remove
the armed tryboot.txt + staged artifacts, and reboot to stock.
"""

import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pqc_boot import rollback, ssh
from pqc_boot.cli import app
from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot.stages import deploy


def _ctx(tmp_path, ip="10.0.0.5") -> Context:
    return Context.create(Config(workspace=Path(tmp_path), pi_ip=ip))


def test_run_without_pi_ip_raises(tmp_path):
    ctx = Context.create(Config(workspace=Path(tmp_path)))
    with pytest.raises(RuntimeError, match="requires the Pi IP"):
        rollback.run(ctx)


def test_rollback_aborts_when_no_backup(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(ssh, "connect",
                        lambda ip, user: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ssh, "sudo_checked",
                        lambda c, cmd, **k: types.SimpleNamespace(ok=True))
    # `test -f <backup>` returns not-ok -> no backup present
    monkeypatch.setattr(ssh, "run_remote",
                        lambda c, cmd: types.SimpleNamespace(ok="test -f" not in cmd))
    with pytest.raises(RuntimeError, match="no backup"):
        rollback.run(ctx)


def test_rollback_restores_removes_and_reboots(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    sudo: list[str] = []
    remote: list[str] = []
    monkeypatch.setattr(ssh, "connect",
                        lambda ip, user: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ssh, "sudo_checked",
                        lambda c, cmd, **k: sudo.append((cmd, k)) or types.SimpleNamespace(ok=True))
    monkeypatch.setattr(ssh, "sudo_run",
                        lambda c, cmd, **k: remote.append((cmd, k)) or types.SimpleNamespace(ok=True))
    monkeypatch.setattr(ssh, "run_remote",
                        lambda c, cmd: types.SimpleNamespace(ok=True))  # test -f backup

    rollback.run(ctx)

    b = deploy.BOOT_DIR
    cmds = [cmd for cmd, _ in sudo]
    # restore config.txt from backup, atomically (stage + mv)
    assert any(deploy.BACKUP_DIR in c and "config.txt.new" in c and "mv" in c for c in cmds)
    # remove tryboot.txt + the staged artifacts
    rm = [c for c in cmds if c.startswith("rm -f")]
    assert rm and "tryboot.txt" in rm[0]
    assert f"{b}/u-boot.bin" in rm[0] and f"{b}/u-boot.dtb" in rm[0]
    # reboot to stock (via sudo_run), with the password threaded
    assert any("reboot" in cmd for cmd, _ in remote)
    assert all("password" in k for _, k in sudo)


def test_rollback_cli_command_exists():
    res = CliRunner().invoke(app, ["rollback", "--help"])
    assert res.exit_code == 0
    assert "--ip" in res.output and "stock boot" in res.output.lower()
