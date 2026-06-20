"""build: two-pass cross-compile of U-Boot, embedding the ML-DSA public key.

This is where the runtime Claude touchpoint lives: on compile/link failure the
build_fixer is invoked (bounded retry, every fix shown as a diff). See
pqc_boot/ai/build_fixer.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context


def plan(ctx: "Context") -> str:
    return (f"cross-compile U-Boot ({ctx.config.defconfig}, aarch64-linux-gnu-); "
            "two-pass embed of ML-DSA-44 pubkey into the control DTB")


def run(ctx: "Context") -> None:
    raise NotImplementedError(
        "build stage not yet implemented: configure defconfig, cross-compile, "
        "embed the pubkey (pass 2); on failure call ai.build_fixer (bounded retry)"
    )
