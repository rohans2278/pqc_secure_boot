"""build: two-pass cross-compile of U-Boot, embedding the ML-DSA public key.

Deterministic by default (the patch is pinned and proven), matching the PoC flow:

  pass 1  make rpi_arm64_defconfig CROSS_COMPILE=aarch64-linux-gnu-
          make CROSS_COMPILE=aarch64-linux-gnu- -j<nproc>      -> u-boot.dtb + tools
  embed   tools/fdt_add_pubkey -a sha256,mldsa44 -k <keydir> -n <name> -r conf u-boot-pubkey.dtb
          (writes /signature/key-<name>{algo=sha256,mldsa44, required=conf,
           mldsa,public-key=1312 B})
  pass 2  make CROSS_COMPILE=aarch64-linux-gnu- EXT_DTB=<...>/u-boot-pubkey.dtb -j<nproc>
          -> final u-boot.bin with the pubkey-bearing control DTB embedded.

Pass 1 produces the plain control DTB and the host tools; the pubkey is added to a
copy of it via fdt_add_pubkey (decoupled from kernel-FIT signing, which is the sign
stage); pass 2 re-embeds that DTB into the U-Boot binary. See docs/integration.md
§8/§9. The runtime injection in §10 (the key that actually governs verification on
the Pi 5) reuses this same u-boot-pubkey.dtb off the SD card.

Derived, never hardcoded: the u-boot-pubkey.dtb size (the boot script's cp.b length,
the PoC's 0xba98) is the on-disk size of the artifact this stage emits — the deploy
boot-script generator reads it from the file, so a rebuilt DTB of any size is correct.

This is also where the runtime Claude touchpoint lives: on compile/link failure the
build_fixer is invoked (bounded retry, every fix screened + shown + confirmed before
apply). See pqc_boot/ai/build_fixer.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..ai import build_fixer

if TYPE_CHECKING:
    from ..context import Context

CROSS_COMPILE = "aarch64-linux-gnu-"
ALGO = "sha256,mldsa44"
# Mark the configuration signature MANDATORY (fail-closed): a tampered image is
# rejected rather than verified opportunistically. This matches the proven PoC dtb,
# whose /signature/key-mykey carries required="conf". fdt_add_pubkey -r writes it.
REQUIRE = "conf"
PUBKEY_DTB = "u-boot-pubkey.dtb"  # the pubkey-bearing control DTB (artifact)
PUB_KEY_BYTES = 1312

# Proof the patch was applied (the vendored target verifier). Mirrors the patch stage.
_PATCH_SENTINEL = "lib/ml-dsa/mldsa-verify.c"


def plan(ctx: "Context") -> str:
    return (f"cross-compile U-Boot ({ctx.config.defconfig}, {CROSS_COMPILE}); two-pass "
            f"embed of the ML-DSA-44 pubkey into the control DTB ({PUBKEY_DTB})")


def _jobs() -> str:
    return str(os.cpu_count() or 1)


def _make(ctx: "Context", *args: str):
    """One make invocation in the U-Boot tree, with CROSS_COMPILE set."""
    return ctx.run(["make", "-C", str(ctx.uboot_dir), f"CROSS_COMPILE={CROSS_COMPILE}",
                    *args], check=False)


def _make_or_fix(ctx: "Context", label: str, *args: str) -> None:
    """Run a make pass; on failure, hand off to the bounded AI fix loop (seeded with
    this failure's stderr — no redundant rebuild), then fail hard if still broken.
    The deterministic path never touches Claude."""
    proc = _make(ctx, *args)
    if proc is None or proc.returncode == 0:   # None == dry-run
        return
    ctx.warn(f"build: '{label}' failed; attempting AI-assisted self-correction")
    if _try_fix(ctx, label, make_args=args, first_error=proc.stderr):
        return
    raise RuntimeError(
        f"build: '{label}' failed and could not be repaired:\n{proc.stderr.strip()}"
    )


def run(ctx: "Context") -> None:
    uboot = ctx.uboot_dir
    if not (uboot / ".git").exists():
        raise RuntimeError(f"U-Boot tree not found at {uboot}; run the clone stage first")
    if not (uboot / _PATCH_SENTINEL).exists():
        raise RuntimeError(f"{uboot} is not patched (missing {_PATCH_SENTINEL}); "
                           "run the patch stage first")
    pub = ctx.keydir / f"{ctx.config.keyname}.pub"
    if not ctx.dry_run and not pub.is_file():
        raise RuntimeError(f"public key not found at {pub}; run the keys stage first")

    jobs = _jobs()

    # Configure + pass 1: build the control DTB and the host tools (mkimage,
    # fdt_add_pubkey). fdt_add_pubkey only exists once tools are built.
    _make_or_fix(ctx, "configure", ctx.config.defconfig)
    _make_or_fix(ctx, "pass 1 (build)", f"-j{jobs}")

    # Embed the pubkey into a copy of the freshly built control DTB.
    _embed_pubkey(ctx)

    # Pass 2: re-embed the pubkey-bearing DTB into the U-Boot binary.
    ext_dtb = (uboot / PUBKEY_DTB).resolve()
    _make_or_fix(ctx, "pass 2 (embed)", f"EXT_DTB={ext_dtb}", f"-j{jobs}")

    if ctx.dry_run:
        return

    # Verify the artifacts the later stages depend on actually exist.
    binary = uboot / "u-boot.bin"
    dtb = uboot / PUBKEY_DTB
    for art in (binary, dtb):
        if not art.is_file():
            raise RuntimeError(f"build finished but expected artifact is missing: {art}")

    # Derived value (never hardcoded): the on-disk DTB size is the boot script's cp.b
    # length. Echo it so it's visible; deploy reads it straight off the file.
    size = dtb.stat().st_size
    ctx.info(f"[green]✓[/green] built u-boot.bin + {PUBKEY_DTB} "
             f"({size} B / 0x{size:x} — cp.b length for the boot script)")


def _embed_pubkey(ctx: "Context") -> None:
    """Copy the built control DTB and write the ML-DSA pubkey node into the copy."""
    uboot = ctx.uboot_dir
    src = uboot / "u-boot.dtb"
    dst = uboot / PUBKEY_DTB
    fdt_add_pubkey = uboot / "tools" / "fdt_add_pubkey"

    if ctx.dry_run:
        ctx.run(["cp", str(src), str(dst)])
        ctx.run([str(fdt_add_pubkey), "-a", ALGO, "-k", str(ctx.keydir),
                 "-n", ctx.config.keyname, "-r", REQUIRE, str(dst)])
        return

    if not src.is_file():
        raise RuntimeError(f"pass 1 did not produce {src}")
    if not fdt_add_pubkey.is_file():
        raise RuntimeError(f"pass 1 did not build {fdt_add_pubkey}")
    shutil.copy2(src, dst)

    proc = ctx.run([str(fdt_add_pubkey), "-a", ALGO, "-k", str(ctx.keydir),
                    "-n", ctx.config.keyname, "-r", REQUIRE, str(dst)], check=False)
    if proc is not None and proc.returncode != 0:
        raise RuntimeError(f"fdt_add_pubkey failed to embed the pubkey:\n{proc.stderr.strip()}")

    _assert_pubkey_node(ctx, dst)


def _assert_pubkey_node(ctx: "Context", dtb: Path) -> None:
    """Confirm the embed landed exactly as the proven PoC dtb: algo=sha256,mldsa44,
    required=conf (the fail-closed enforcement), and a 1312-byte public key.

    Uses fdtget directly (a read-only probe, not a build action) — provided by the
    device-tree-compiler package that doctor already checks via `dtc`.
    """
    node = f"/signature/key-{ctx.config.keyname}"

    def _prop(name: str) -> tuple[int, str]:
        p = subprocess.run(["fdtget", str(dtb), node, name],
                           capture_output=True, text=True)
        return p.returncode, (p.stdout.strip() or p.stderr.strip())

    # algo is the full FIT algo string (fdt_add_pubkey stores info->name verbatim).
    rc, algo = _prop("algo")
    if rc != 0 or algo != ALGO:
        raise RuntimeError(f"pubkey embed check failed: {node}/algo = "
                           f"{algo!r} (want {ALGO!r})")
    # required=conf makes the configuration signature mandatory (tampered images are
    # rejected, not verified opportunistically) — the proven PoC's security property.
    rc, required = _prop("required")
    if rc != 0 or required != REQUIRE:
        raise RuntimeError(f"pubkey embed check failed: {node}/required = "
                           f"{required!r} (want {REQUIRE!r} — fail-closed enforcement)")
    # fdtget -t bx prints the bytes space-separated; count them to confirm 1312 B.
    raw = subprocess.run(["fdtget", "-t", "bx", str(dtb), node, "mldsa,public-key"],
                         capture_output=True, text=True)
    n = len(raw.stdout.split()) if raw.returncode == 0 else -1
    if n != PUB_KEY_BYTES:
        raise RuntimeError(f"pubkey embed check failed: mldsa,public-key is {n} B "
                           f"(want {PUB_KEY_BYTES})")


def _try_fix(ctx: "Context", label: str, *, make_args, first_error: str) -> bool:
    """Drive the build-fixer loop for a failed make pass, seeded with first_error."""

    def build_once() -> tuple[bool, str]:
        proc = _make(ctx, *make_args)
        if proc is None:
            return True, ""
        return proc.returncode == 0, proc.stderr

    def gather_context(error: str) -> dict[str, str]:
        return _excerpt_sources(ctx.uboot_dir, error)

    def confirm(diff: str, screen) -> bool:
        return _confirm_fix(ctx, label, diff, screen)

    return build_fixer.run_fix_loop(
        ctx, build_once=build_once, gather_context=gather_context, confirm=confirm,
        first_error=first_error,
    )


def _excerpt_sources(uboot: Path, error: str, *, max_files: int = 4,
                     context_lines: int = 40) -> dict[str, str]:
    """Pull short excerpts of the in-tree files named in a compiler error."""
    import re

    out: dict[str, str] = {}
    for m in re.finditer(r"([\w./-]+\.[ch]):(\d+)", error):
        rel, line = m.group(1), int(m.group(2))
        rel = rel.lstrip("./")
        path = uboot / rel
        if rel in out or not path.is_file():
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        lo = max(0, line - 1 - context_lines)
        hi = min(len(lines), line - 1 + context_lines)
        out[rel] = "\n".join(lines[lo:hi])
        if len(out) >= max_files:
            break
    return out


def _confirm_fix(ctx: "Context", label: str, diff: str, screen) -> bool:
    """Surface a screened-ok AI diff and ask the user to approve applying it."""
    ctx.info(f"\n[bold]Claude proposed a fix for '{label}':[/bold]")
    ctx.info(f"[dim]files: {', '.join(screen.modified) or '(none)'}[/dim]")
    ctx.info(diff)
    try:
        reply = input("Apply this fix? [y/N] ").strip().lower()
    except EOFError:
        return False
    return reply in ("y", "yes")
