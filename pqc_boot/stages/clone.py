"""clone: fetch the hard-pinned U-Boot source."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context


def plan(ctx: "Context") -> str:
    return f"git clone {ctx.config.uboot_repo} @ {ctx.config.uboot_tag} -> {ctx.uboot_dir}"


def run(ctx: "Context") -> None:
    dest = ctx.uboot_dir
    if dest.exists() and any(dest.iterdir()):
        ctx.warn(f"u-boot already present at {dest}; skipping clone")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx.run([
        "git", "clone", "--depth", "1",
        "--branch", ctx.config.uboot_tag,
        ctx.config.uboot_repo, str(dest),
    ])
