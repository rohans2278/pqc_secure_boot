"""Host prerequisite checks for `pqc-boot doctor`.

doctor doesn't just report — for end users it detects each prerequisite, asks for
confirmation, and installs the missing ones (system packages via apt, Python deps
via pip). The goal is that a user needs only an API key and a Pi IP, not a manual
setup checklist.

Things that can't be auto-installed (the API key, Pi reachability) are reported.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass


class InstallDeclined(Exception):
    """Raised when the user declines the install confirmation."""


class PrereqInstallError(RuntimeError):
    """Raised when an install command can't run or fails."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    apt_pkg: str | None = None   # system package that provides this, if missing
    pip_pkg: str | None = None   # python package to pip-install, if missing

    @property
    def fixable(self) -> bool:
        return not self.ok and (self.apt_pkg is not None or self.pip_pkg is not None)


# Required binaries -> the apt package that provides them.
_BINARIES = [
    ("git", "git"),
    ("make", "build-essential"),
    ("mkimage", "u-boot-tools"),
    ("aarch64-linux-gnu-gcc", "gcc-aarch64-linux-gnu"),
    ("dtc", "device-tree-compiler"),
]

# Required Python modules (lazy deps) -> pip package.
_PY_MODULES = [
    ("fabric", "fabric"),
    ("anthropic", "anthropic"),
]

MIN_PY = (3, 11)


def _check_python() -> Check:
    ok = sys.version_info[:2] >= MIN_PY
    ver = ".".join(map(str, sys.version_info[:3]))
    return Check("python>=3.11", ok, f"found {ver}")


def _check_binary(binary: str, apt_pkg: str) -> Check:
    path = shutil.which(binary)
    if path:
        return Check(binary, True, path)
    return Check(binary, False, "not found", apt_pkg=apt_pkg)


def _check_module(module: str, pip_pkg: str) -> Check:
    found = importlib.util.find_spec(module) is not None
    return Check(
        f"py:{module}", found,
        "importable" if found else "not installed",
        pip_pkg=None if found else pip_pkg,
    )


def _check_api_key() -> Check:
    ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return Check(
        "ANTHROPIC_API_KEY", ok,
        "set" if ok else "not set (export ANTHROPIC_API_KEY=...)",
    )


def check_pi_reachable(ip: str, port: int = 22, timeout: float = 3.0) -> Check:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return Check(f"pi ssh {ip}:{port}", True, "reachable")
    except OSError as e:
        return Check(f"pi ssh {ip}:{port}", False, f"unreachable ({e})")


def check_all(pi_ip: str | None = None) -> list[Check]:
    checks = [_check_python()]
    checks += [_check_binary(b, pkg) for b, pkg in _BINARIES]
    checks += [_check_module(m, pkg) for m, pkg in _PY_MODULES]
    checks.append(_check_api_key())
    if pi_ip:
        checks.append(check_pi_reachable(pi_ip))
    return checks


def plan_installs(checks: list[Check]) -> tuple[list[str], list[str]]:
    """Return (apt_pkgs, pip_pkgs) needed to fix the fixable failing checks."""
    apt_pkgs = sorted({c.apt_pkg for c in checks if c.fixable and c.apt_pkg})
    pip_pkgs = sorted({c.pip_pkg for c in checks if c.fixable and c.pip_pkg})
    return apt_pkgs, pip_pkgs


def install_missing(
    checks: list[Check],
    *,
    confirm: Callable[[list[str], list[str]], bool],
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[str]:
    """Detect + confirm + install missing prerequisites.

    `confirm(apt_pkgs, pip_pkgs)` is called before anything is installed; it should
    show the packages and return True to proceed. Returns the actions taken.

    Raises:
        InstallDeclined: the user said no (caller should exit cleanly).
        PrereqInstallError: an installer couldn't run (no sudo / no TTY) or failed.
    """
    apt_pkgs, pip_pkgs = plan_installs(checks)
    if not apt_pkgs and not pip_pkgs:
        return []

    if not confirm(apt_pkgs, pip_pkgs):
        raise InstallDeclined()

    actions: list[str] = []
    try:
        if apt_pkgs:
            runner(["sudo", "apt-get", "update"], check=True)
            runner(["sudo", "apt-get", "install", "-y", *apt_pkgs], check=True)
            actions.append(f"apt installed: {', '.join(apt_pkgs)}")
        if pip_pkgs:
            runner([sys.executable, "-m", "pip", "install", *pip_pkgs], check=True)
            actions.append(f"pip installed: {', '.join(pip_pkgs)}")
    except FileNotFoundError as e:
        # e.g. `sudo` not present at all.
        raise PrereqInstallError(
            f"could not run installer ({e}). Install manually: "
            + _manual_hint(apt_pkgs, pip_pkgs)
        ) from e
    except subprocess.CalledProcessError as e:
        # Non-zero exit, e.g. sudo couldn't authenticate (no TTY) or apt failed.
        raise PrereqInstallError(
            f"install command failed (exit {e.returncode}). Install manually: "
            + _manual_hint(apt_pkgs, pip_pkgs)
        ) from e
    return actions


def _manual_hint(apt_pkgs: list[str], pip_pkgs: list[str]) -> str:
    parts = []
    if apt_pkgs:
        parts.append(f"sudo apt-get install -y {' '.join(apt_pkgs)}")
    if pip_pkgs:
        parts.append(f"pip install {' '.join(pip_pkgs)}")
    return "; ".join(parts)
