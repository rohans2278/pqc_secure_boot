"""The migrate engine: ordered stages + the orchestrator that walks them.

Each stage module implements plan(ctx)->str and run(ctx)->None. `run_pipeline`
runs them in order, skipping ones already completed (per PipelineState), and
supports starting mid-pipeline (`from_stage`) and re-running (`force`).
"""

from __future__ import annotations

from types import ModuleType

from .context import Context
from .stages import build, clone, deploy, keys, patch, sign, verify

# Canonical stage order.
STAGES: list[tuple[str, ModuleType]] = [
    ("clone", clone),
    ("keys", keys),
    ("patch", patch),
    ("build", build),
    ("sign", sign),
    ("deploy", deploy),
    ("verify", verify),
]

STAGE_NAMES = [name for name, _ in STAGES]
_STAGE_MAP = dict(STAGES)


def get_stage(name: str) -> ModuleType:
    try:
        return _STAGE_MAP[name]
    except KeyError:
        raise ValueError(
            f"unknown stage '{name}'; valid: {', '.join(STAGE_NAMES)}"
        ) from None


def run_stage(ctx: Context, name: str, *, force: bool = False) -> None:
    """Run a single stage, honoring state unless forced."""
    mod = get_stage(name)
    if ctx.dry_run:
        # Dry-run shows the plan only; never executes a stage.
        ctx.info(f"[bold]→ {name}[/bold] (dry-run): {mod.plan(ctx)}")
        return
    if ctx.state.is_done(name) and not force:
        ctx.info(f"[green]✓[/green] {name}: already done (use --force to redo)")
        return
    ctx.info(f"[bold]→ {name}[/bold]: {mod.plan(ctx)}")
    mod.run(ctx)
    ctx.state.mark_done(name)


def run_pipeline(ctx: Context, *, force: bool = False,
                 from_stage: str | None = None) -> None:
    """Run the full pipeline in order."""
    start = 0
    if from_stage:
        start = STAGE_NAMES.index(from_stage)  # raises ValueError if unknown
    for name, _ in STAGES[start:]:
        run_stage(ctx, name, force=force)
