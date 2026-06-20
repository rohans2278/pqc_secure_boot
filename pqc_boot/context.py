"""The shared object handed to every pipeline stage.

Rather than threading config/state/output/flags through every function, each stage
receives one Context. It also exposes `run()` — the single chokepoint for shell
commands — which echoes what it does and becomes a no-op under --dry-run, so the
whole pipeline honours dry-run for free.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .config import Config
from .state import PipelineState


@dataclass
class Context:
    config: Config
    state: PipelineState
    console: Console
    dry_run: bool = False

    @classmethod
    def create(cls, config: Config, *, dry_run: bool = False,
               console: Console | None = None) -> "Context":
        return cls(
            config=config,
            state=PipelineState.load(config.workspace),
            console=console or Console(),
            dry_run=dry_run,
        )

    # --- derived paths ---
    @property
    def workspace(self) -> Path:
        return self.config.workspace

    @property
    def uboot_dir(self) -> Path:
        return self.workspace / "u-boot"

    @property
    def keydir(self) -> Path:
        return self.config.keydir

    # --- output helpers ---
    def info(self, msg: str) -> None:
        self.console.print(msg)

    def warn(self, msg: str) -> None:
        self.console.print(f"[yellow]{msg}[/yellow]")

    # --- command runner ---
    def run(self, cmd: list[str], *, cwd: Path | None = None,
            check: bool = True) -> subprocess.CompletedProcess | None:
        """Run a shell command. Echoes it; under dry_run prints only and returns None."""
        printable = " ".join(shlex.quote(c) for c in cmd)
        self.console.print(f"[dim]$ {printable}[/dim]")
        if self.dry_run:
            return None
        return subprocess.run(
            cmd, cwd=cwd, check=check, text=True, capture_output=True
        )
