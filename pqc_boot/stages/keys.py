"""keys: generate the ML-DSA-44 keypair (mldsa-native).

Mirrors the PoC keygen: crypto_sign_keypair() -> raw 1312 B public key and
2560 B secret key, written to the fixed keydir. The private key stays on this host.

Self-contained: compiles the vendored mldsa-native (pqc_boot/_mldsa) at runtime, so
the keypair matches the on-device verifier byte-for-byte without depending on the
U-Boot tree or any PoC path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context

PUB_BYTES = 1312
PRIV_BYTES = 2560

# Vendored mldsa-native sources live next to this package (pqc_boot/_mldsa).
_MLDSA_DIR = Path(__file__).resolve().parent.parent / "_mldsa"


def plan(ctx: "Context") -> str:
    name = ctx.config.keyname
    return (f"generate ML-DSA-44 keypair (mldsa-native) -> "
            f"{ctx.keydir}/{name}.pub (1312 B), {ctx.keydir}/{name}.bin (2560 B)")


def run(ctx: "Context") -> None:
    keydir = ctx.keydir
    keyname = ctx.config.keyname
    pub = keydir / f"{keyname}.pub"
    priv = keydir / f"{keyname}.bin"

    # Protective skip: never silently clobber an existing private key. A regenerated
    # key would invalidate any already-embedded/deployed public key.
    if (priv.exists() and priv.stat().st_size == PRIV_BYTES
            and pub.exists() and pub.stat().st_size == PUB_BYTES):
        ctx.warn(f"keypair already present at {keydir}; skipping "
                 "(delete the keydir to regenerate)")
        return

    keydir.mkdir(parents=True, exist_ok=True)
    os.chmod(keydir, 0o700)

    # Compile the keygen helper. -DUSE_HOSTCC is REQUIRED: the vendored config gates
    # the keypair/sign APIs behind #ifndef USE_HOSTCC.
    keygen_bin = ctx.workspace / "keygen"
    ctx.run([
        "cc", "-O2", "-DUSE_HOSTCC", "-DMLD_CONFIG_PARAMETER_SET=44",
        f"-I{_MLDSA_DIR}",
        str(_MLDSA_DIR / "keygen.c"),
        str(_MLDSA_DIR / "mldsa_native.c"),
        "-o", str(keygen_bin),
    ])
    ctx.run([str(keygen_bin), str(keydir), keyname])

    # keygen.c writes the raw bytes but sets no permissions.
    os.chmod(priv, 0o600)
    os.chmod(pub, 0o644)
    os.chmod(keydir, 0o700)

    if priv.stat().st_size != PRIV_BYTES or pub.stat().st_size != PUB_BYTES:
        raise RuntimeError(
            f"unexpected ML-DSA-44 key sizes: {priv.name}={priv.stat().st_size} "
            f"(want {PRIV_BYTES}), {pub.name}={pub.stat().st_size} (want {PUB_BYTES})"
        )

    ctx.info(f"[green]✓[/green] keypair -> {keydir} "
             f"({pub.name} {PUB_BYTES} B, {priv.name} {PRIV_BYTES} B; private key chmod 600)")
