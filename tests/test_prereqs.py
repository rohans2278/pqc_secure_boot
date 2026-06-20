import subprocess

import pytest

from pqc_boot import prereqs
from pqc_boot.prereqs import (
    Check,
    InstallDeclined,
    PrereqInstallError,
    check_all,
    install_missing,
    plan_installs,
)


def test_check_all_includes_core_checks():
    names = {c.name for c in check_all()}
    assert "python>=3.11" in names
    assert "mkimage" in names
    assert "ANTHROPIC_API_KEY" in names
    # no pi check unless an ip is given
    assert not any(n.startswith("pi ssh") for n in names)


def test_check_all_adds_pi_check_with_ip():
    names = {c.name for c in check_all(pi_ip="203.0.113.1")}
    assert any(n.startswith("pi ssh 203.0.113.1") for n in names)


def test_plan_installs_collects_fixable():
    checks = [
        Check("mkimage", False, "not found", apt_pkg="u-boot-tools"),
        Check("py:fabric", False, "missing", pip_pkg="fabric"),
        Check("git", True, "/usr/bin/git"),
        Check("ANTHROPIC_API_KEY", False, "not set"),  # not fixable
    ]
    apt, pip = plan_installs(checks)
    assert apt == ["u-boot-tools"]
    assert pip == ["fabric"]


def test_install_missing_nothing_to_do():
    checks = [Check("git", True, "ok")]
    assert install_missing(checks, confirm=lambda a, p: True) == []


def test_install_missing_declined_runs_nothing():
    checks = [Check("mkimage", False, "x", apt_pkg="u-boot-tools")]
    calls = []
    with pytest.raises(InstallDeclined):
        install_missing(
            checks,
            confirm=lambda a, p: False,
            runner=lambda *a, **k: calls.append(a),
        )
    assert calls == []


def test_install_missing_confirmed_runs_apt_and_pip():
    checks = [
        Check("mkimage", False, "x", apt_pkg="u-boot-tools"),
        Check("py:fabric", False, "x", pip_pkg="fabric"),
    ]
    cmds = []

    def fake_runner(cmd, **kw):
        cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    actions = install_missing(checks, confirm=lambda a, p: True, runner=fake_runner)
    # apt update + apt install + pip install
    assert ["sudo", "apt-get", "update"] in cmds
    assert any(c[:4] == ["sudo", "apt-get", "install", "-y"] for c in cmds)
    assert any("pip" in c for c in cmds)
    assert any("u-boot-tools" in a for a in actions)


def test_install_missing_failure_is_clean_error():
    checks = [Check("mkimage", False, "x", apt_pkg="u-boot-tools")]

    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(PrereqInstallError) as ei:
        install_missing(checks, confirm=lambda a, p: True, runner=boom)
    assert "install manually" in str(ei.value).lower()


def test_install_missing_no_sudo_is_clean_error():
    checks = [Check("mkimage", False, "x", apt_pkg="u-boot-tools")]

    def no_sudo(cmd, **kw):
        raise FileNotFoundError("sudo")

    with pytest.raises(PrereqInstallError):
        install_missing(checks, confirm=lambda a, p: True, runner=no_sudo)
