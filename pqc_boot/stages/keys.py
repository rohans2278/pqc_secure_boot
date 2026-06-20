"""keys: generate the ML-DSA-44 keypair (mldsa-native).

Mirrors the PoC keygen: crypto_sign_keypair() -> raw 1312 B public key and
2560 B secret key, written to the fixed keydir. The private key stays on this host.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context


def plan(ctx: "Context") -> str:
    name = ctx.config.keyname
    return (f"generate ML-DSA-44 keypair (mldsa-native) -> "
            f"{ctx.keydir}/{name}.pub (1312 B), {ctx.keydir}/{name}.bin (2560 B)")


def run(ctx: "Context") -> None:
    raise NotImplementedError(
        "keys stage not yet implemented: vendor mldsa-native (param set 44), "
        "build keygen, and emit the raw pub/priv keypair into the keydir"
    )
