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


def run_remote(conn: "Connection", command: str, *, hide: bool = True, in_stream=None):
    """Run a command on the Pi. warn=True: failures return (ok=False), not raise.

    in_stream, when given, feeds the command's stdin (used to hand sudo a password via
    `sudo -S` without ever putting it on the command line).
    """
    kwargs = {"hide": hide, "warn": True}
    if in_stream is not None:
        kwargs["in_stream"] = in_stream
    return conn.run(command, **kwargs)


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


def _sudo(conn: "Connection", command: str, password: str | None, hide: bool):
    """Run `command` under sudo. SECURITY: the password is fed only through stdin
    (`sudo -S`), never placed in the command string (which is visible in ps/logs).
    `-p ''` suppresses sudo's prompt. password=None -> non-interactive `sudo -n`."""
    if password:
        import io

        return run_remote(conn, f"sudo -S -p '' {command}", hide=hide,
                          in_stream=io.StringIO(password + "\n"))
    return run_remote(conn, f"sudo -n {command}", hide=hide)


def sudo_checked(conn: "Connection", command: str, *,
                 password: str | None = None, hide: bool = True):
    """Run a command under sudo, raising a clear error on failure (wrong password, or
    no passwordless sudo) instead of hanging on a prompt."""
    result = _sudo(conn, command, password, hide)
    if not result.ok:
        err = (result.stderr or "").strip()
        if password and ("incorrect password" in err or "try again" in err):
            raise SSHCommandError(
                f"sudo rejected the password on the Pi (incorrect?): {command!r}"
            )
        if not password and ("password is required" in err or "a terminal is required" in err):
            raise SSHCommandError(
                "the Pi's sudo needs a password but none was given — run interactively "
                f"or set PQCBOOT_SUDO_PASSWORD (command: {command!r})"
            )
        raise SSHCommandError(
            f"remote sudo command failed (exit {result.exited}): {command!r}: {err}"
        )
    return result


def sudo_run(conn: "Connection", command: str, *,
             password: str | None = None, hide: bool = True):
    """Like sudo_checked but does NOT raise — for commands whose connection drop is
    expected (e.g. `reboot`). Password still via stdin only."""
    return _sudo(conn, command, password, hide)


def push_root(conn: "Connection", local: str, remote: str,
              *, password: str | None = None, staging: str = "/tmp/pqcboot") -> None:
    """Copy a local file to a root-owned path on the Pi.

    conn.put can't write privileged locations like /boot/firmware, so stage into a
    user-writable tmp dir, then `sudo cp` into place. Uses the Pi sudo password (via
    stdin) when supplied, else passwordless `sudo -n`.
    """
    import posixpath
    import shlex

    staged = posixpath.join(staging, posixpath.basename(remote))
    run_checked(conn, f"mkdir -p {shlex.quote(staging)}")
    conn.put(local, staged)
    sudo_checked(conn, f"cp {shlex.quote(staged)} {shlex.quote(remote)}",
                 password=password)


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
