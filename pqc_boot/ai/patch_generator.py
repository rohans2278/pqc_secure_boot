"""Maintainer-only Claude touchpoint: (re)generate the pinned RSA->ML-DSA patch.

`pqc-boot generate-patch` reproduces patches/uboot-2026.04-mldsa44.diff from
first principles, deterministic where it can be and using Claude only where reasoning
is needed (locating the RSA insertion points in a clean tree):

  1. clone a pristine v2026.04 tree;
  2. vendor the mldsa-native CORE deterministically from pqc_boot/_mldsa;
  3. write the 5 U-Boot WRAPPER files deterministically from pqc_boot/patch_assets;
  4. for each of the 6 in-tree edits, ask Claude for a MINIMAL diff that places the
     documented change at the right RSA anchor, screened to an ALLOWLIST of exactly
     those 6 files (this is generate-patch-specific — distinct from build_fixer's
     PROTECTED_PATHS denylist, since these files are intentionally edited here);
  5. emit the unified diff with `git diff`;
  6. VERIFY the candidate applies to a fresh clean tree and builds host mkimage with
     the ML-DSA objects linked, and that it touches the expected 46 files;
  7. adopt it as the pinned patch only after that verification + maintainer confirm.

Scope note: this reproduces the v2026.04 patch (the 46-file expectation is specific to
that tag). It is NOT yet validated against any other U-Boot version.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from . import build_fixer

if TYPE_CHECKING:
    from ..context import Context

_PKG = Path(__file__).resolve().parent.parent          # pqc_boot/
_REPO = _PKG.parent                                     # repo root
MLDSA_CORE_DIR = _PKG / "_mldsa"
WRAPPER_ASSETS_DIR = _PKG / "patch_assets"
PINNED_PATCH = _REPO / "patches" / "uboot-2026.04-mldsa44.diff"

CROSS_COMPILE = "aarch64-linux-gnu-"
EXPECTED_FILE_COUNT = 46                                # 40 added + 6 modified (v2026.04)

# The 6 in-tree edits Claude must locate + make. Allowlist: the regenerated edit diff
# may touch ONLY these paths. (Deliberately a generate-patch allowlist, NOT the
# build_fixer crypto denylist — image-sig*.c are edited here on purpose.)
EXPECTED_EDITS: dict[str, str] = {
    "boot/image-sig.c":
        "Add the single line `#include <u-boot/ml-dsa.h>` immediately after the "
        "existing `#include <u-boot/rsa.h>` line. Change nothing else.",
    "tools/image-sig-host.c":
        "Two changes: (1) add `#include <u-boot/ml-dsa.h>` after the existing "
        "`#include <u-boot/rsa.h>` line; (2) append this entry to the static "
        "`crypto_algos[]` array, immediately after the secp521r1/ecdsa entry:\n"
        "\t{\n\t\t.name = \"mldsa44\",\n\t\t.key_len = MLDSA44_PUBLICKEYBYTES,\n"
        "\t\t.sign = mldsa_sign,\n\t\t.add_verify_data = mldsa_add_verify_data,\n"
        "\t\t.verify = mldsa_verify,\n\t},",
    "lib/Kconfig":
        "Add the line `source \"lib/ml-dsa/Kconfig\"` immediately after the existing "
        "`source \"lib/rsa/Kconfig\"` line.",
    "lib/Makefile":
        "Add the line `obj-$(CONFIG_ML_DSA) += ml-dsa/` immediately after the existing "
        "`obj-$(CONFIG_$(PHASE_)RSA) += rsa/` line.",
    "tools/Makefile":
        "Three additions for the host (mkimage) ML-DSA build: (1) after the "
        "`ECDSA_OBJS-$(CONFIG_TOOLS_LIBCRYPTO) := ...` line, add:\n"
        "MLDSA_OBJS-$(CONFIG_FIT_SIGNATURE) := $(addprefix generated/lib/ml-dsa/, \\\n"
        "\t\t\t\t\tmldsa-sign.o mldsa-verify.o \\\n\t\t\t\t\tmldsa_native.o)\n"
        "(2) after the ecdsa-libcrypto HOSTCFLAGS line, add per-object include flags:\n"
        "HOSTCFLAGS_generated/lib/ml-dsa/mldsa_native.o += -I$(srctree)/lib/ml-dsa\n"
        "HOSTCFLAGS_generated/lib/ml-dsa/mldsa-sign.o += -I$(srctree)/lib/ml-dsa\n"
        "HOSTCFLAGS_generated/lib/ml-dsa/mldsa-verify.o += -I$(srctree)/lib/ml-dsa\n"
        "(3) add `$(MLDSA_OBJS-y) \\` into the mkimage/dumpimage object list, between "
        "the `$(RSA_OBJS-y) \\` and `$(AES_OBJS-y)` lines.",
    "configs/rpi_arm64_defconfig":
        "Enable verified boot. (a) Flip these two in place: "
        "`CONFIG_EFI_RUNTIME_UPDATE_CAPSULE=y` -> "
        "`# CONFIG_EFI_RUNTIME_UPDATE_CAPSULE is not set`, and "
        "`CONFIG_EFI_CAPSULE_FIRMWARE_RAW=y` -> "
        "`# CONFIG_EFI_CAPSULE_FIRMWARE_RAW is not set`. (b) Append these lines:\n"
        "CONFIG_FIT=y\nCONFIG_FIT_SIGNATURE=y\nCONFIG_OF_CONTROL=y\n"
        "CONFIG_OF_SEPARATE=y\nCONFIG_ML_DSA=y\nCONFIG_ML_DSA_VERIFY=y\n"
        "CONFIG_BOOTDELAY=-2\n# CONFIG_EFI_LOADER is not set\n"
        "CONFIG_LEGACY_IMAGE_FORMAT=y",
}

_SYSTEM_PROMPT = (
    "You are a U-Boot maintainer integrating ML-DSA-44 FIT verification. Given one "
    "source file and the EXACT change required, return ONLY a minimal unified diff "
    "(inside a single ```diff fenced block) that makes that change at the correct "
    "anchor in THAT file. Use `a/<path>` and `b/<path>` headers with the given path. "
    "Touch only the given file; change nothing unrelated."
)


def plan(ctx: "Context") -> str:
    return ("regenerate patches/uboot-2026.04-mldsa44.diff: clone v2026.04, vendor "
            "mldsa-native + wrappers, locate the 6 RSA edits via Claude, verify "
            "(apply + build host mkimage), then adopt")


# --- deterministic assembly -------------------------------------------------

def vendor_core(tree: Path) -> None:
    """Copy the mldsa-native core from pqc_boot/_mldsa into <tree>/lib/ml-dsa
    (everything except the host-only keygen.c, which is not part of the patch)."""
    dst = tree / "lib" / "ml-dsa"
    dst.mkdir(parents=True, exist_ok=True)
    for src in MLDSA_CORE_DIR.rglob("*"):
        if src.name == "keygen.c" or not src.is_file():
            continue
        rel = src.relative_to(MLDSA_CORE_DIR)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def write_wrappers(tree: Path) -> None:
    """Write the 5 U-Boot wrapper files from pqc_boot/patch_assets into the tree."""
    for src in WRAPPER_ASSETS_DIR.rglob("*"):
        if not src.is_file():
            continue
        out = tree / src.relative_to(WRAPPER_ASSETS_DIR)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


# --- Claude-located edits ---------------------------------------------------

def request_edit(ctx: "Context", rel_path: str, file_text: str, instruction: str) -> str | None:
    """Ask Claude for a minimal unified diff that makes one documented edit. Returns
    the diff text, or None. Transmits the file + instruction to the Anthropic API."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        f"File: {rel_path}\n\n--- current contents ---\n{file_text}\n"
        f"--- required change ---\n{instruction}\n\n"
        "Return only the minimal unified diff."
    )
    resp = client.messages.create(
        model=ctx.config.model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return build_fixer.extract_diff(text)


def _apply_edit(ctx: "Context", tree: Path, rel_path: str, instruction: str) -> None:
    """Locate + apply one edit, screened to touch ONLY rel_path."""
    src = tree / rel_path
    if not src.is_file():
        raise RuntimeError(f"expected file {rel_path} not in the clean tree")
    diff = request_edit(ctx, rel_path, src.read_text(), instruction)
    if not diff:
        raise RuntimeError(f"Claude returned no diff for {rel_path}")
    try:
        touched = build_fixer.files_changed_by_apply(diff, tree)
    except build_fixer.DiffApplyError as e:
        raise RuntimeError(f"edit diff for {rel_path} does not apply: {e}") from e
    # ALLOWLIST: this edit may touch only its own file.
    if touched != [rel_path]:
        raise RuntimeError(
            f"edit for {rel_path} touched unexpected files {touched} (allowlist violation)"
        )
    proc = subprocess.run(["git", "-C", str(tree), "apply", "-"],
                          input=diff, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"failed to apply edit for {rel_path}: {proc.stderr.strip()}")
    ctx.info(f"[dim]located + applied edit: {rel_path}[/dim]")


# --- tree helpers -----------------------------------------------------------

def _clone_clean(ctx: "Context", dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx.run(["git", "clone", "--depth", "1", "--branch", ctx.config.uboot_tag,
             ctx.config.uboot_repo, str(dest)])


def _git(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True)


# --- verification -----------------------------------------------------------

def verify_candidate(ctx: "Context", candidate: str) -> None:
    """Clone a fresh clean tree, apply the candidate, assert the 46-file footprint,
    and build host mkimage with the ML-DSA objects linked. Raises on any failure."""
    vtree = ctx.workspace / "patchgen" / "verify"
    _clone_clean(ctx, vtree)

    numstat = subprocess.run(["git", "-C", str(vtree), "apply", "--numstat", "-"],
                             input=candidate, text=True, capture_output=True)
    if numstat.returncode != 0:
        raise RuntimeError(f"candidate patch does not apply: {numstat.stderr.strip()}")
    n = len([l for l in numstat.stdout.splitlines() if l.strip()])
    if n != EXPECTED_FILE_COUNT:
        raise RuntimeError(f"candidate touches {n} files, expected {EXPECTED_FILE_COUNT} "
                           "(v2026.04)")

    apply = subprocess.run(["git", "-C", str(vtree), "apply", "-"],
                           input=candidate, text=True, capture_output=True)
    if apply.returncode != 0:
        raise RuntimeError(f"candidate failed to apply: {apply.stderr.strip()}")

    ctx.run(["make", "-C", str(vtree), f"CROSS_COMPILE={CROSS_COMPILE}",
             ctx.config.defconfig])
    mk = ctx.run(["make", "-C", str(vtree), f"CROSS_COMPILE={CROSS_COMPILE}", "tools"],
                 check=False)
    if mk is not None and mk.returncode != 0:
        raise RuntimeError(f"host tools build failed with the candidate:\n{mk.stderr.strip()}")
    if not (vtree / "tools" / "mkimage").is_file():
        raise RuntimeError("candidate built but tools/mkimage is missing")
    ctx.info(f"[green]✓[/green] candidate verified: applies ({n} files) + builds host mkimage")


# --- orchestration ----------------------------------------------------------

def run(ctx: "Context", *, force: bool = False) -> None:
    if not WRAPPER_ASSETS_DIR.is_dir():
        raise RuntimeError(f"wrapper assets missing at {WRAPPER_ASSETS_DIR}")

    if ctx.dry_run:
        ctx.info(f"[bold]generate-patch (dry-run)[/bold]: {plan(ctx)}")
        for rel in EXPECTED_EDITS:
            ctx.info(f"[dim]  would locate + edit {rel}[/dim]")
        return

    tree = ctx.workspace / "patchgen" / "u-boot"
    _clone_clean(ctx, tree)

    vendor_core(tree)
    write_wrappers(tree)
    for rel, instruction in EXPECTED_EDITS.items():
        _apply_edit(ctx, tree, rel, instruction)

    # Emit the unified diff for everything staged against the pristine v2026.04 HEAD.
    _git(tree, "add", "-A")
    diff = _git(tree, "diff", "--cached", "--no-color")
    if diff.returncode != 0:
        raise RuntimeError(f"git diff failed: {diff.stderr.strip()}")
    candidate = diff.stdout

    verify_candidate(ctx, candidate)

    if not force:
        try:
            reply = input(f"Overwrite {PINNED_PATCH.name} with the verified candidate? [y/N] ")
        except EOFError:
            reply = ""
        if reply.strip().lower() not in ("y", "yes"):
            ctx.warn("not adopted; pinned patch left unchanged")
            return

    PINNED_PATCH.write_text(candidate)
    ctx.info(f"[green]✓[/green] wrote {PINNED_PATCH} — review with `git diff`")
