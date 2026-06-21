"""Tests for the SSH sudo layer — focused on the security invariant: the Pi sudo
password is fed ONLY via stdin (sudo -S), never placed in the command string (which
would be visible in `ps`/logs).
"""

import types

import pytest

from pqc_boot import ssh


class FakeConn:
    """Records every conn.run, reading any in_stream so tests can inspect what was
    fed to stdin vs what was in the command string."""

    def __init__(self, ok=True, stderr=""):
        self.calls: list[dict] = []
        self._ok = ok
        self._stderr = stderr

    def run(self, command, **kwargs):
        instream = kwargs.get("in_stream")
        fed = instream.read() if instream is not None else None
        self.calls.append({"command": command, "kwargs": kwargs, "stdin": fed})
        return types.SimpleNamespace(
            ok=self._ok, stderr=self._stderr, exited=0 if self._ok else 1, stdout=""
        )

    def put(self, local, remote):
        self.calls.append({"put": (local, remote)})


def test_password_goes_to_stdin_never_into_command():
    c = FakeConn(ok=True)
    ssh.sudo_checked(c, "cp a b", password="hunter2")
    call = c.calls[-1]
    assert call["command"].startswith("sudo -S -p ''")
    assert "cp a b" in call["command"]
    assert "hunter2" not in call["command"]   # NEVER in argv
    assert call["stdin"] == "hunter2\n"        # ONLY via stdin


def test_passwordless_uses_dash_n_and_no_stdin():
    c = FakeConn(ok=True)
    ssh.sudo_checked(c, "cp a b")
    call = c.calls[-1]
    assert call["command"].startswith("sudo -n ")
    assert call["stdin"] is None


def test_wrong_password_raises_distinguishable_error():
    c = FakeConn(ok=False, stderr="Sorry, try again.\nsudo: 1 incorrect password attempt")
    with pytest.raises(ssh.SSHCommandError, match="rejected the password"):
        ssh.sudo_checked(c, "cp a b", password="bad")


def test_password_required_but_missing_raises():
    c = FakeConn(ok=False, stderr="sudo: a password is required")
    with pytest.raises(ssh.SSHCommandError, match="needs a password"):
        ssh.sudo_checked(c, "cp a b")


def test_sudo_run_does_not_raise_and_keeps_password_off_argv():
    c = FakeConn(ok=False, stderr="boom")
    r = ssh.sudo_run(c, "reboot", password="pw")   # must not raise even on failure
    assert r.ok is False
    assert "pw" not in c.calls[-1]["command"]
    assert c.calls[-1]["stdin"] == "pw\n"


def test_push_root_threads_password_via_stdin_only():
    c = FakeConn(ok=True)
    ssh.push_root(c, "/local/f", "/boot/firmware/f", password="pw")
    sudo_calls = [x for x in c.calls if x.get("command", "").startswith("sudo")]
    assert sudo_calls, "push_root should issue a sudo cp"
    assert "pw" not in sudo_calls[-1]["command"]
    assert sudo_calls[-1]["stdin"] == "pw\n"
