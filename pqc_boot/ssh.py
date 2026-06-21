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


def sudo_checked(conn: "Connection", command: str, *, hide: bool = True):
    """Run a command under non-interactive sudo (`sudo -n`), raising a clear error
    if the SSH user lacks passwordless sudo (rather than hanging on a prompt)."""
    result = run_remote(conn, f"sudo -n {command}", hide=hide)
    if not result.ok:
        err = (result.stderr or "").strip()
        if "password is required" in err or "a terminal is required" in err:
            raise SSHCommandError(
                "passwordless sudo required on the Pi for boot-file changes "
                f"(sudo -n failed): {command!r}"
            )
        raise SSHCommandError(
            f"remote sudo command failed (exit {result.exited}): {command!r}: {err}"
        )
    return result


def push_root(conn: "Connection", local: str, remote: str,
              *, staging: str = "/tmp/pqcboot") -> None:
    """Copy a local file to a root-owned path on the Pi.

    conn.put can't write privileged locations like /boot/firmware, so stage into a
    user-writable tmp dir, then `sudo -n cp` into place. Requires passwordless sudo
    for the SSH user (standard on Raspberry Pi OS); fails fast otherwise.
    """
    import posixpath
    import shlex

    staged = posixpath.join(staging, posixpath.basename(remote))
    run_checked(conn, f"mkdir -p {shlex.quote(staging)}")
    conn.put(local, staged)
    sudo_checked(conn, f"cp {shlex.quote(staged)} {shlex.quote(remote)}")


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
