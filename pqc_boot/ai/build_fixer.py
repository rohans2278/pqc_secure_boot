"""Runtime Claude touchpoint: self-correct U-Boot build failures.

This is the security-critical AI surface, so it is hardened in code, not by trusting
the prompt:

  1. Diff screening (screen_diff): the files a diff changes are obtained from git's
     own parser (`git apply --numstat`), NOT from regexing the diff headers, then
     HARD-REJECTED if any path is a crypto/verification path or escapes the tree —
     regardless of what the system prompt said. PROTECTED_PATHS was cross-checked to
     exist in a clean v2026.04 tree; no real crypto file is silently absent.
  2. Confirmation gate (run_fix_loop): a screened-ok diff is applied only after
     explicit user approval — surfacing is not enough for a bootloader patch.
  3. Disclosure (DISCLOSURE): we send the build error + source excerpts to the
     Anthropic API; the caller must show this to the user first.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context

DISCLOSURE = (
    "pqc-boot will send the U-Boot build error and relevant source excerpts to the "
    "Anthropic API (Claude) to propose a fix. No keys are sent."
)

SYSTEM_PROMPT = (
    "You are a U-Boot build engineer. Given a cross-compilation error and source "
    "context, return the MINIMAL unified diff that fixes the build. Output ONLY the "
    "diff inside a single ```diff fenced block. Never alter cryptographic or "
    "signature-verification logic; fix only what the error requires. "
    "(A code-level guardrail rejects diffs touching crypto paths regardless.)"
)

# Crypto / verification paths, each cross-checked to exist in a clean v2026.04 tree.
# A diff touching any of these is rejected outright. Directory entries end with "/".
PROTECTED_PATHS = (
    # FIT signature verification (runtime/target)
    "boot/image-sig.c",
    "boot/image-fit-sig.c",
    "boot/image-host.c",
    # host-side signing / key tooling
    "tools/image-sig-host.c",
    "tools/image-host.c",
    "tools/fdt_add_pubkey.c",
    "tools/fit_check_sign.c",
    "tools/preload_check_sign.c",
    "tools/key2dtsi.py",
    "tools/iot2050-sign-fw.sh",
    # EFI secure-boot signature verification
    "lib/efi_loader/efi_signature.c",
    # signature-crypto libraries (NOTE: lib/crypt/ is password hashing -> NOT here)
    "lib/rsa/",
    "lib/ecdsa/",
    "lib/crypto/",
    "lib/libavb/",
    "lib/mbedtls/",
)

# Substrings that mark the post-quantum additions wherever they are placed.
PROTECTED_SUBSTRINGS = ("mldsa", "ml-dsa", "ml_dsa", "dilithium")

_DIFF_RE = re.compile(r"```diff\s*\n(.*?)```", re.DOTALL)


class DiffApplyError(RuntimeError):
    """`git apply --numstat` could not parse/stage the proposed diff."""


def build_fix_prompt(make_error: str, source_excerpts: dict[str, str]) -> str:
    """Construct the user prompt from the build error and relevant source."""
    parts = ["The U-Boot cross-compile failed with this error:\n", make_error.strip()]
    for path, text in source_excerpts.items():
        parts.append(f"\n--- {path} ---\n{text}")
    parts.append("\nReturn the minimal fix as a unified diff in a ```diff block.")
    return "\n".join(parts)


def extract_diff(response_text: str) -> str | None:
    """Pull the unified diff out of a ```diff fenced block, if present."""
    m = _DIFF_RE.search(response_text)
    return m.group(1).strip() if m else None


def is_protected(path: str) -> bool:
    """True if a path is crypto/verification code the fixer must never touch."""
    p = path.lower().lstrip("./")
    if any(s in p for s in PROTECTED_SUBSTRINGS):
        return True
    for prot in PROTECTED_PATHS:
        if prot.endswith("/"):
            if p.startswith(prot):
                return True
        elif p == prot or p.endswith("/" + prot):
            return True
    return False


def _is_safe_path(path: str) -> bool:
    """Reject absolute paths, traversal, and .git metadata (must stay in-tree)."""
    p = path.strip()
    if not p or p.startswith("/"):
        return False
    if ".." in p.split("/"):
        return False
    if p.startswith(".git/"):
        return False
    return True


def diff_modified_files(diff: str) -> list[str]:
    """Regex fallback for listing a diff's files (used when no git repo is given)."""
    files: set[str] = set()
    for line in diff.splitlines():
        path: str | None = None
        if line.startswith(("+++ ", "--- ")):
            path = line[4:].split("\t")[0].strip()
        elif line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
        if not path or path == "/dev/null":
            continue
        if path[:2] in ("a/", "b/"):
            path = path[2:]
        files.add(path)
    return sorted(files)


def files_changed_by_apply(diff_text: str, repo_dir, runner=subprocess.run) -> list[str]:
    """Authoritative file list per git's own parser, via `git apply --numstat`.

    Does not modify the tree. Preferred over diff_modified_files because it trusts
    git, not a hand-rolled regex of the proposed diff's headers. Raises
    DiffApplyError if git can't parse/apply the diff.
    """
    proc = runner(
        ["git", "apply", "--numstat", "-"],
        input=diff_text, cwd=str(repo_dir), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise DiffApplyError(f"git apply --numstat failed: {proc.stderr.strip()}")
    files: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files.append(parts[2].strip())
    return files


@dataclass
class DiffScreen:
    modified: list[str]
    protected_hits: list[str]   # crypto/verification paths -> always reject
    unsafe_paths: list[str]     # absolute / traversal / .git -> reject

    @property
    def ok(self) -> bool:
        return not self.protected_hits and not self.unsafe_paths

    def reason(self) -> str:
        bits = []
        if self.protected_hits:
            bits.append(f"touches protected crypto/verify paths: {self.protected_hits}")
        if self.unsafe_paths:
            bits.append(f"unsafe paths: {self.unsafe_paths}")
        return "; ".join(bits) or "ok"


def screen_files(files: list[str]) -> DiffScreen:
    protected = [f for f in files if is_protected(f)]
    unsafe = [f for f in files if f not in protected and not _is_safe_path(f)]
    return DiffScreen(sorted(files), protected, unsafe)


def screen_diff(diff: str, *, repo_dir=None, runner=subprocess.run) -> DiffScreen:
    """Screen an AI-proposed diff. Uses git's parser when repo_dir is given.

    May raise DiffApplyError when repo_dir is set and the diff won't parse/apply;
    callers (run_fix_loop) must catch that and discard the diff, never crash.
    """
    if repo_dir is not None:
        files = files_changed_by_apply(diff, repo_dir, runner)
    else:
        files = diff_modified_files(diff)
    return screen_files(files)


def request_fix(
    ctx: "Context",
    make_error: str,
    source_excerpts: dict[str, str],
) -> str | None:
    """Ask Claude for a fix diff. Returns the diff text, or None if none was given.

    NOTE: this transmits the error + source_excerpts to the Anthropic API. Callers
    must have shown DISCLOSURE first.
    """
    import anthropic  # lazy

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=ctx.config.model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_fix_prompt(make_error, source_excerpts)}
        ],
    )
    text = "".join(
        block.text for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    return extract_diff(text)


def apply_diff(diff: str, repo_dir, runner=subprocess.run) -> bool:
    """Apply an already-screened diff to the tree via `git apply`. Returns success."""
    proc = runner(
        ["git", "apply", "-"],
        input=diff, cwd=str(repo_dir), text=True, capture_output=True,
    )
    return proc.returncode == 0


def run_fix_loop(ctx: "Context", *, build_once, gather_context, confirm,
                 first_error: str) -> bool:
    """Bounded self-correction loop for a failed U-Boot build.

    Contract (security-critical — enforced here, not by trusting the prompt):
      - DISCLOSURE is shown once before any source is sent to the Anthropic API.
      - `first_error` seeds the FIRST iteration, so the loop reuses the failure that
        triggered it instead of rebuilding immediately (no redundant build).
      - `build_once()` runs make, returns (ok: bool, error: str); used only AFTER a
        fix is applied, to check whether it worked and to refresh the error.
      - `gather_context(error)` returns {path: excerpt} for the prompt.
      - Each attempt (up to ctx.config.build_fix_attempts): request_fix ->
        screen_diff(diff, repo_dir=ctx.uboot_dir). A DiffApplyError (won't apply) or
        a screen failure (crypto/verify/unsafe paths) DISCARDS the diff — it is never
        applied — and the loop tries again; neither ever propagates as a crash.
      - A screened-ok diff is applied ONLY after confirm(diff, screen) returns True;
        a declined fix aborts the loop.
      - Returns True iff the build eventually succeeds.
    """
    ctx.info(DISCLOSURE)
    error = first_error
    for attempt in range(1, ctx.config.build_fix_attempts + 1):
        ctx.info(f"[dim]build-fixer attempt {attempt}/{ctx.config.build_fix_attempts}[/dim]")

        try:
            diff = request_fix(ctx, error, gather_context(error))
        except Exception as e:  # network/API failure -> give up cleanly, never crash
            ctx.warn(f"build-fixer: could not get a fix from Claude ({e}); aborting")
            return False
        if not diff:
            ctx.warn("build-fixer: Claude returned no diff; aborting")
            return False

        try:
            screen = screen_diff(diff, repo_dir=ctx.uboot_dir)
        except DiffApplyError as e:
            ctx.warn(f"build-fixer: proposed diff does not apply ({e}); discarding")
            continue
        if not screen.ok:
            ctx.warn(f"build-fixer: REJECTED diff ({screen.reason()}); discarding")
            continue

        if not confirm(diff, screen):
            ctx.warn("build-fixer: fix not approved; aborting")
            return False

        if not apply_diff(diff, ctx.uboot_dir):
            ctx.warn("build-fixer: approved diff failed to apply; discarding")
            continue

        ok, error = build_once()
        if ok:
            ctx.info("[green]✓[/green] build succeeded after AI fix")
            return True

    ctx.warn("build-fixer: exhausted attempts without a working build")
    return False
