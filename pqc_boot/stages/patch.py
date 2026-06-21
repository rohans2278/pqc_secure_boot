"""patch: apply the pinned RSA->ML-DSA diff to the U-Boot tree.

Deterministic: applies patches/uboot-2026.04-mldsa44.diff verbatim (vendors
mldsa-native, registers the `sha256,mldsa44` algo, rewires the FIT verify path).
Idempotent — re-running on an already-patched tree is a no-op.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context

PATCH_NAME = "uboot-2026.04-mldsa44.diff"
# Repo-root patches/ dir, resolved relative to this file (pqc_boot/stages/patch.py).
PATCH_PATH = Path(__file__).resolve().parents[2] / "patches" / PATCH_NAME

# Sentinels that must exist after a successful apply.
_SENTINEL_FILE = "lib/ml-dsa/mldsa-verify.c"
_SENTINEL_DEFCONFIG = ("configs/rpi_arm64_defconfig", "CONFIG_ML_DSA=y")


def plan(ctx: "Context") -> str:
    return f"apply patches/{PATCH_NAME} to {ctx.uboot_dir}"


def _git(uboot: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(uboot), *args],
        capture_output=True, text=True, check=check,
    )


def run(ctx: "Context") -> None:
    uboot = ctx.uboot_dir
    if not (uboot / ".git").exists():
        raise RuntimeError(
            f"U-Boot tree not found at {uboot}; run the clone stage first"
        )
    if not PATCH_PATH.is_file():
        raise RuntimeError(f"pinned patch not found at {PATCH_PATH}")

    # Idempotent: if the patch already reverse-applies cleanly, it's already applied.
    if _git(uboot, "apply", "--reverse", "--check", str(PATCH_PATH),
            check=False).returncode == 0:
        ctx.warn(f"patch already applied to {uboot}; skipping")
        return

    # Validate it applies forward, then apply it (echoed via ctx.run).
    check = _git(uboot, "apply", "--check", str(PATCH_PATH), check=False)
    if check.returncode != 0:
        raise RuntimeError(
            f"patch does not apply cleanly to {uboot} (tree not a pristine "
            f"{ctx.config.uboot_tag}?):\n{check.stderr.strip()}"
        )
    ctx.run(["git", "apply", "--whitespace=nowarn", str(PATCH_PATH)], cwd=uboot)

    # Verify the expected changes landed.
    defconfig, needle = _SENTINEL_DEFCONFIG
    if not (uboot / _SENTINEL_FILE).exists() or \
            needle not in (uboot / defconfig).read_text():
        raise RuntimeError("patch applied but expected ML-DSA changes are missing")

    ctx.info(f"[green]✓[/green] applied {PATCH_NAME} to {uboot}")
