"""pqc-boot command-line interface.

Deliberately thin: each command parses flags, builds a Context, and delegates to
pipeline.py / prereqs.py / ai. No migration logic lives here.

A bare `pqc-boot migrate` is interactive — it runs the prerequisite check/install
itself and prompts for the Pi IP, SSH user, and sudo password — so the user needs no
flags and no separate `doctor` run. Flags still work for non-interactive use.

(Intentionally does NOT use `from __future__ import annotations` — Typer introspects
the runtime annotations to build options, and stringized annotations break that.)
"""

import os
import subprocess
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import pipeline, prereqs
from .config import DEFAULT_MODEL, Config
from .context import Context

# Env var carrying the Pi sudo password for NON-interactive runs (when --ip is given).
# An env var, never a flag — a flag would expose the password in `ps`/shell history.
SUDO_PASSWORD_ENV = "PQCBOOT_SUDO_PASSWORD"

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Migrate a Raspberry Pi 5 boot chain from RSA to post-quantum ML-DSA-44.",
)

console = Console()


def _build_context(
    ip: Optional[str], user: str, model: str, dry_run: bool,
    sudo_password: Optional[str] = None,
) -> Context:
    """Resolve flags into a Config + Context (the object every stage receives)."""
    cfg = Config(pi_ip=ip, pi_user=user, model=model, sudo_password=sudo_password)
    return Context.create(cfg, dry_run=dry_run)


def _render_checks(checks: list) -> None:
    table = Table(title="pqc-boot doctor")
    table.add_column("check", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("detail")
    for c in checks:
        status = "[green]OK[/green]" if c.ok else "[red]MISSING[/red]"
        table.add_row(c.name, status, c.detail)
    console.print(table)


def _ensure_prereqs(ip: Optional[str], *, assume_yes: bool) -> list:
    """Check prerequisites, auto-install the fixable ones, and return the checks still
    failing afterward. Shared by `doctor` and `migrate`. May raise InstallDeclined /
    PrereqInstallError (callers decide how to react)."""
    checks = prereqs.check_all(pi_ip=ip)
    _render_checks(checks)

    def confirm(apt_pkgs: list, pip_pkgs: list) -> bool:
        if assume_yes:
            return True
        parts = []
        if apt_pkgs:
            parts.append(f"apt: {', '.join(apt_pkgs)}")
        if pip_pkgs:
            parts.append(f"pip: {', '.join(pip_pkgs)}")
        console.print(f"[yellow]Will install → {'; '.join(parts)}[/yellow]")
        return typer.confirm("Install them now?")

    actions = prereqs.install_missing(checks, confirm=confirm)
    if actions:
        for a in actions:
            console.print(f"[green]✓[/green] {a}")
        checks = prereqs.check_all(pi_ip=ip)
        _render_checks(checks)
    return [c for c in checks if not c.ok]


def _resolve_connection(ip: Optional[str], user: str):
    """Resolve (ip, user, sudo_password). When --ip is omitted, prompt interactively
    (the sudo password is read hidden); otherwise stay non-interactive and take the
    password from the env var. The password is only ever held in memory for this run.
    """
    if ip is None:
        ip = typer.prompt("Raspberry Pi IP address")
        user = typer.prompt("SSH user", default=user)
        pw = typer.prompt(
            "Pi sudo password (leave blank for passwordless sudo)",
            hide_input=True, default="", show_default=False,
        ) or None
    else:
        pw = os.environ.get(SUDO_PASSWORD_ENV) or None
    return ip, user, pw


@app.command()
def migrate(
    ip: Optional[str] = typer.Option(
        None, "--ip", help="Raspberry Pi IP address (prompted for if omitted)."
    ),
    user: str = typer.Option("pi", "--user", help="SSH user on the Pi."),
    from_stage: Optional[str] = typer.Option(
        None, "--from", help="Start at this stage instead of the beginning."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run stages already marked done."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what each stage would do; execute nothing."
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", help="Claude model for build self-correction."
    ),
) -> None:
    """Run the full pipeline: clone → keys → patch → build → sign → deploy → verify."""
    if from_stage is not None and from_stage not in pipeline.STAGE_NAMES:
        raise typer.BadParameter(
            f"unknown stage '{from_stage}'; valid: {', '.join(pipeline.STAGE_NAMES)}",
            param_hint="--from",
        )

    sudo_password = None
    if not dry_run:
        # Resolve the essentials (interactive when --ip is omitted), then fold in the
        # prerequisite check/install so the user never has to call `doctor` separately.
        ip, user, sudo_password = _resolve_connection(ip, user)
        try:
            failing = _ensure_prereqs(ip, assume_yes=False)
        except prereqs.InstallDeclined:
            console.print("[yellow]Prerequisites are required to continue; aborting.[/yellow]")
            raise typer.Exit(1)
        except prereqs.PrereqInstallError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if failing:
            console.print(
                f"[yellow]{len(failing)} prerequisite(s) still failing; continuing "
                "(some, e.g. the API key, are only needed if a build self-correction "
                "is triggered).[/yellow]"
            )

    ctx = _build_context(ip, user, model, dry_run, sudo_password=sudo_password)
    try:
        pipeline.run_pipeline(ctx, force=force, from_stage=from_stage)
    except NotImplementedError as e:
        ctx.warn(f"stage not implemented yet: {e}")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        cmd = " ".join(e.cmd) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
        ctx.warn(f"command failed (exit {e.returncode}): {cmd}")
        if e.stderr:
            console.print(e.stderr)
        raise typer.Exit(1)


@app.command()
def doctor(
    ip: Optional[str] = typer.Option(
        None, "--ip", help="Also check the Pi is reachable over SSH."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Install missing prerequisites without prompting."
    ),
) -> None:
    """Check host prerequisites (toolchain, deps, API key) and install what's missing."""
    try:
        failing = _ensure_prereqs(ip, assume_yes=yes)
    except prereqs.InstallDeclined:
        console.print("[yellow]Declined; nothing installed.[/yellow]")
        raise typer.Exit(1)
    except prereqs.PrereqInstallError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if failing:
        console.print(
            f"[red]{len(failing)} prerequisite(s) still failing "
            "(some can't be auto-installed, e.g. the API key or Pi reachability).[/red]"
        )
        raise typer.Exit(1)
    console.print("[green]All prerequisites satisfied.[/green]")


@app.command()
def rollback(
    ip: Optional[str] = typer.Option(
        None, "--ip", help="Raspberry Pi IP address (prompted for if omitted)."
    ),
    user: str = typer.Option("pi", "--user", help="SSH user on the Pi."),
) -> None:
    """Restore the Pi's stock boot from backup (undo a deploy/promote) and reboot."""
    from . import rollback as rollback_mod

    ip, user, sudo_password = _resolve_connection(ip, user)
    ctx = _build_context(ip, user, DEFAULT_MODEL, dry_run=False,
                         sudo_password=sudo_password)
    try:
        rollback_mod.run(ctx)
    except RuntimeError as e:
        ctx.warn(str(e))
        raise typer.Exit(1)


@app.command(name="generate-patch")
def generate_patch(
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Claude model to use."),
    force: bool = typer.Option(
        False, "--force", help="Adopt the regenerated patch without the confirm prompt."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print actions without cloning, calling Claude, or writing."
    ),
) -> None:
    """MAINTAINER ONLY: (re)generate the pinned RSA→ML-DSA patch via Claude."""
    from .ai import patch_generator

    ctx = _build_context(None, "pi", model, dry_run)
    try:
        patch_generator.run(ctx, force=force)
    except subprocess.CalledProcessError as e:
        cmd = " ".join(e.cmd) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
        ctx.warn(f"command failed (exit {e.returncode}): {cmd}")
        if e.stderr:
            console.print(e.stderr)
        raise typer.Exit(1)
    except RuntimeError as e:
        ctx.warn(str(e))
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
