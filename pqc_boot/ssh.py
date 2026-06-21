"""SSH transport to the Pi (deploy/verify edge).

Everything that touches the actual Pi goes through here, wrapping Fabric. Fabric is
imported lazily inside functions so that `doctor`, `clone`, and the tests run even
before Fabric is installed.

Note: run_remote uses warn=True, so a failed remote command RETURNS a result
(non-zero exit) instead of raising. Callers must inspect `.ok`. read_cmdline
enforces this: a failed `cat` raises SSHCommandError rather than silently looking
like an empty/unverified cmdline.

Stubs for the deploy/verify file operations — filled in with those stages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabric import Connection


class SSHCommandError(RuntimeError):
    """A remote command failed to execute (distinct from a successful command
    whose output simply doesn't contain what we're looking for)."""


def connect(ip: str, user: str = "pi") -> "Connection":
    """Open a Fabric SSH connection to the Pi."""
    from fabric import Connection  # lazy: only needed for deploy/verify

    return Connection(host=ip, user=user)


def run_remote(conn: "Connection", command: str, *, hide: bool = True):
    """Run a command on the Pi. warn=True: failures return (ok=False), not raise."""
    return conn.run(command, hide=hide, warn=True)


def run_checked(conn: "Connection", command: str, *, hide: bool = True):
    """Run a remote command and raise SSHCommandError on non-zero exit."""
    result = run_remote(conn, command, hide=hide)
    if not result.ok:
        raise SSHCommandError(
            f"remote command failed (exit {result.exited}): {command!r}: "
            f"{(result.stderr or '').strip()}"
        )
    return result


def push(conn: "Connection", local: str, remote: str) -> None:
    """Copy a local file to the Pi."""
    conn.put(local, remote)


def fetch(conn: "Connection", remote: str, local: str) -> None:
    """Copy a file from the Pi to the local host (mirror of push)."""
    conn.get(remote, local)


def backup_boot(conn: "Connection") -> None:
    """Back up the Pi's existing boot files before any overwrite."""
    raise NotImplementedError("ssh.backup_boot: implement with the deploy stage")


def deploy_tryboot(conn: "Connection", artifacts: dict[str, str]) -> None:
    """Stage artifacts to the tryboot (A/B) slot. On failure the caller cleans up."""
    raise NotImplementedError("ssh.deploy_tryboot: implement with the deploy stage")


def read_cmdline(conn: "Connection") -> str:
    """Return /proc/cmdline from the Pi.

    Raises SSHCommandError if the read itself fails, so verify can distinguish a
    failed read from a successful read that lacks the verified marker.
    """
    return run_checked(conn, "cat /proc/cmdline").stdout.strip()
