"""patch: apply the pinned RSA->ML-DSA diff to the U-Boot tree.

Deterministic: applies patches/uboot-2026.04-mldsa44.diff verbatim (vendors
mldsa-native, registers the `sha256,mldsa44` algo, rewires the FIT verify path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context

PATCH_NAME = "uboot-2026.04-mldsa44.diff"


def plan(ctx: "Context") -> str:
    return f"apply patches/{PATCH_NAME} to {ctx.uboot_dir}"


def run(ctx: "Context") -> None:
    raise NotImplementedError(
        f"patch stage not yet implemented: apply patches/{PATCH_NAME} "
        "(reconstructed from the PoC) into the cloned U-Boot tree"
    )
