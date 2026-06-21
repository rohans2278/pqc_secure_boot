"""Tests for the patch stage.

The integration test applies the real pinned patch to a throwaway git worktree of
the cloned U-Boot tree (so it never mutates the real workspace) and asserts the
ML-DSA changes landed and that re-running is idempotent. It skips when the clone
isn't present.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot.stages import patch

REPO = Path(__file__).resolve().parents[1]
CLONE = REPO / ".pqcboot-work" / "u-boot"
GIT = shutil.which("git")
needs_clone = pytest.mark.skipif(
    GIT is None or not (CLONE / ".git").exists(),
    reason="cloned v2026.04 u-boot not present",
)


def test_plan_mentions_patch_name(tmp_path):
    ctx = Context.create(Config(workspace=tmp_path))
    assert patch.PATCH_NAME in patch.plan(ctx)


def test_pinned_patch_file_exists():
    assert patch.PATCH_PATH.is_file(), f"missing {patch.PATCH_PATH}"


@needs_clone
def test_apply_real_patch_and_idempotent(tmp_path):
    wt = tmp_path / "u-boot"  # Context.uboot_dir = workspace/"u-boot"
    subprocess.run(
        [GIT, "-C", str(CLONE), "worktree", "add", "--detach", str(wt), "HEAD"],
        check=True, capture_output=True, text=True,
    )
    try:
        ctx = Context.create(Config(workspace=tmp_path))
        patch.run(ctx)
        assert (wt / "lib/ml-dsa/mldsa-verify.c").exists()
        assert "CONFIG_ML_DSA=y" in (wt / "configs/rpi_arm64_defconfig").read_text()
        # Idempotent: a second run detects the applied patch and skips without error.
        patch.run(ctx)
        assert (wt / "lib/ml-dsa/mldsa-verify.c").exists()
    finally:
        subprocess.run(
            [GIT, "-C", str(CLONE), "worktree", "remove", "--force", str(wt)],
            capture_output=True, text=True,
        )
