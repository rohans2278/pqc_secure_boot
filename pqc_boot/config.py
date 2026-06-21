"""Configuration for pqc-boot.

Defaults live in code. The only thing a user must supply is the Pi's IP (`--ip`),
needed for the deploy/verify stages. Everything else has a sensible default; there
is intentionally no app-config file to maintain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# U-Boot is HARD-PINNED to the version the PoC + pinned patch target. The pinned
# diff is authored against exactly this tag, so we never resolve "latest".
UBOOT_TAG = "v2026.04"
UBOOT_REPO = "https://source.denx.de/u-boot/u-boot.git"
DEFAULT_DEFCONFIG = "rpi_arm64_defconfig"

# Build self-correction model (the only runtime Claude call). Overridable via --model.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BUILD_FIX_ATTEMPTS = 3

# Single source of truth for the verified-boot marker. The reconstructed boot.txt
# writes exactly this into bootargs on the verified path, and the verify stage greps
# /proc/cmdline for exactly this. Keep both sides referencing this constant so they
# can never drift. (Standardized on pqc-boot; the PoC hardware used quboot_verified=1.)
VERIFIED_MARKER = "pqc-boot_verified=1"

# Workspace + keys live in fixed, predictable locations (keydir is NOT configurable).
DEFAULT_WORKSPACE = Path(".pqcboot-work")
KEYS_SUBDIR = "keys"
KEYNAME = "mykey"


@dataclass
class Config:
    """Resolved settings for a pqc-boot run."""

    # The one required-from-user field (only for deploy/verify).
    pi_ip: str | None = None
    pi_user: str = "pi"

    # Pi sudo password for boot-file changes (deploy/verify/rollback). SECURITY:
    # in-memory only for the duration of the run — never persisted (state.py serializes
    # only completed stages) and `repr=False` keeps it out of any logged dataclass repr.
    # Passed to sudo exclusively via stdin (`sudo -S`), never on the command line.
    # None means "assume passwordless sudo" (sudo -n).
    sudo_password: str | None = field(default=None, repr=False)

    # U-Boot source (hard-pinned; see UBOOT_TAG).
    uboot_repo: str = UBOOT_REPO
    uboot_tag: str = UBOOT_TAG
    defconfig: str = DEFAULT_DEFCONFIG

    # Workspace / artifacts.
    workspace: Path = field(default_factory=lambda: DEFAULT_WORKSPACE)

    # Deploy.
    slot: str = "tryboot"

    # Claude (build self-correction).
    model: str = DEFAULT_MODEL
    build_fix_attempts: int = DEFAULT_BUILD_FIX_ATTEMPTS

    @property
    def keydir(self) -> Path:
        """Fixed location for ML-DSA-44 keys; same place every run."""
        return self.workspace / KEYS_SUBDIR

    @property
    def keyname(self) -> str:
        return KEYNAME
