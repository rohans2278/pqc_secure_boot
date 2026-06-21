"""CLI tests for the low-friction migrate flow: interactive prompts when --ip is
omitted (incl. a hidden sudo-password prompt), and dry-run staying non-interactive.
The pipeline + prereq machinery are stubbed so nothing real runs.
"""

from typer.testing import CliRunner

from pqc_boot import cli, pipeline
from pqc_boot.cli import app

runner = CliRunner()


def test_migrate_prompts_when_ip_omitted(monkeypatch):
    captured = {}

    def fake_pipeline(ctx, **kwargs):
        captured["ip"] = ctx.config.pi_ip
        captured["user"] = ctx.config.pi_user
        captured["pw"] = ctx.config.sudo_password

    monkeypatch.setattr(pipeline, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(cli, "_ensure_prereqs", lambda ip, *, assume_yes: [])

    # input lines: Pi IP, SSH user, sudo password (hidden)
    res = runner.invoke(app, ["migrate"], input="10.0.0.9\npiuser\nhunter2\n")

    assert res.exit_code == 0, res.output
    assert captured == {"ip": "10.0.0.9", "user": "piuser", "pw": "hunter2"}
    assert "hunter2" not in res.output   # password is never echoed back


def test_migrate_blank_password_means_passwordless(monkeypatch):
    captured = {}
    monkeypatch.setattr(pipeline, "run_pipeline",
                        lambda ctx, **k: captured.update(pw=ctx.config.sudo_password))
    monkeypatch.setattr(cli, "_ensure_prereqs", lambda ip, *, assume_yes: [])

    res = runner.invoke(app, ["migrate"], input="10.0.0.9\npi\n\n")  # blank password
    assert res.exit_code == 0, res.output
    assert captured["pw"] is None


def test_migrate_dry_run_skips_prompts_and_prereqs(monkeypatch):
    called = {"prereqs": False}
    monkeypatch.setattr(cli, "_ensure_prereqs",
                        lambda *a, **k: called.__setitem__("prereqs", True) or [])
    monkeypatch.setattr(pipeline, "run_pipeline", lambda ctx, **k: None)

    res = runner.invoke(app, ["migrate", "--dry-run"])  # no input provided
    assert res.exit_code == 0, res.output
    assert called["prereqs"] is False    # dry-run never prompts or installs


def test_migrate_ip_flag_reads_password_from_env(monkeypatch):
    captured = {}
    monkeypatch.setattr(pipeline, "run_pipeline",
                        lambda ctx, **k: captured.update(ip=ctx.config.pi_ip,
                                                         pw=ctx.config.sudo_password))
    monkeypatch.setattr(cli, "_ensure_prereqs", lambda ip, *, assume_yes: [])
    monkeypatch.setenv(cli.SUDO_PASSWORD_ENV, "envpass")

    res = runner.invoke(app, ["migrate", "--ip", "10.0.0.5"])  # non-interactive
    assert res.exit_code == 0, res.output
    assert captured == {"ip": "10.0.0.5", "pw": "envpass"}
