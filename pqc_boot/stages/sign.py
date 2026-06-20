"""sign: ML-DSA-44 sign the kernel FIT image.

Mirrors the PoC: a FIT (its) over kernel + fdt + ramdisk, signature algo
`sha256,mldsa44`, key-name-hint `mykey`, signed with the private key via mkimage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context


def plan(ctx: "Context") -> str:
    return ("sign FIT (sha256,mldsa44 over kernel+fdt+ramdisk, key-name-hint "
            f"{ctx.config.keyname}) with the ML-DSA-44 private key")


def run(ctx: "Context") -> None:
    raise NotImplementedError(
        "sign stage not yet implemented: assemble the FIT (.its) and sign it "
        "with mkimage using the ML-DSA-44 private key"
    )
