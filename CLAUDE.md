# pqc-boot

A Python CLI that migrates a **Raspberry Pi 5**'s boot chain from RSA to
**post-quantum ML-DSA-44** verification. It runs the deterministic steps itself —
clone U-Boot 2026.04, cross-compile, ML-DSA-sign, deploy over SSH — and calls the
**Claude API** to reason where rote automation can't. Users need only an
`ANTHROPIC_API_KEY`, **not** a Claude Code account.

This project began as a working proof of concept: U-Boot's RSA verification was
hand-replaced with ML-DSA-44, flashed to a Pi 5, and booted successfully —
confirmed by `pqc-boot_verified=1` in `/proc/cmdline`. pqc-boot turns that manual
exercise into a repeatable tool.

## NEVER ASSUME — verify against the real source (highest priority)

Do not pattern-guess filenames, paths, function names, API shapes, or behavior.
Verify against the actual artifact before writing code that depends on it:
- crypto/verification file paths → check a **clean `v2026.04`** U-Boot tree (not the
  modified PoC tree, not memory);
- what a diff changes → ask the tool that applies it (e.g. `git apply --numstat`),
  not your own regex of the diff headers;
- the verified marker, key sizes, algo strings → confirm from the real PoC artifacts.

If you haven't verified it, say so — don't guess. This is the project's top rule.

## What it actually changes (trust boundary — state this honestly)

- **In scope:** U-Boot verifies the next stage (the kernel **FIT** image) using
  **ML-DSA-44** instead of RSA. That is the link pqc-boot converts.
- **Out of scope:** the Broadcom EEPROM bootloader is RSA-signed by Raspberry Pi
  and **not user-replaceable** — it remains the RSA root of trust. pqc-boot does
  not (and cannot) make the whole chain quantum-safe; do not overstate the claim.

## Core principle: deterministic by default, AI only where it must reason

The security-critical transformation is a **pinned diff** applied verbatim — not
something Claude re-derives each run. There are two Claude touchpoints, and they
fire at very different times:

1. **Runtime — build self-correction (the only AI call end users hit).** When the
   cross-compile fails, pqc-boot sends the error + relevant source/diff context to
   Claude, gets a *minimal* patch, **shows the diff**, applies it, and rebuilds —
   a bounded retry loop (default 3 attempts). AI edits are **never silent**.
2. **Maintainer-only — patch generation (`pqc-boot generate-patch`).** Uses Claude
   to locate where RSA lives in the U-Boot source and (re)produce
   `patches/uboot-2026.04-mldsa44.diff`. This is how the pinned patch was authored;
   end users never run it. It keeps the patch **reproducible**, not hand-waved.

**Guardrails (do not violate):**
- Pin everything: U-Boot tag `v2026.04`, the patch, the toolchain expectations.
- AI-authored changes are always diffed and surfaced — no silent edits to crypto
  or build code.
- **Never overwrite the stable boot slot before `verify` passes** (see Safety).

## Stage pipeline

Resumable, idempotent stages over a workspace dir, tracked by a state file. The
`migrate` command orchestrates them; each is also runnable alone.

| Stage   | Deterministic work                                                          |
|---------|------------------------------------------------------------------------------|
| clone   | `git clone` U-Boot at pinned tag `v2026.04`.                                  |
| keys    | Generate an ML-DSA-44 keypair (mldsa-native, raw 1312 B pub / 2560 B priv)    |
|         | into the keydir; private key stays on the dev host.                          |
| patch   | Apply `patches/uboot-2026.04-mldsa44.diff` (vendor mldsa-native + register the|
|         | `sha256,mldsa44` algo + rewire the FIT verify path).                          |
| build   | Two-pass cross-compile (`aarch64-linux-gnu-`): pass 1 builds the control DTB, |
|         | mkimage adds the pubkey, pass 2 re-embeds it. → build self-correction (Claude)|
|         | on failure. (Runtime injection still governs verification — see Key delivery.)|
| sign    | Sign the FIT (`sha256,mldsa44` over kernel+fdt+ramdisk) with the private key. |
| deploy  | Back up current boot files; push artifacts to the **tryboot** A/B slot (SSH). |
| verify  | One-shot tryboot reboot; SSH back; assert `pqc-boot_verified=1`; **promote**. |

## Public-key delivery (two mechanisms, both real)

The ML-DSA-44 public key reaches U-Boot's verifier **two** ways, and on the Pi 5
the second is what actually counts:

1. **Build-time embed (pass 2).** The standard U-Boot signed-FIT flow: `mkimage`
   adds the pubkey to the control DTB, and a second build pass embeds that DTB
   into the U-Boot binary.
2. **Runtime re-injection (the one that governs verification on Pi 5).** The boot
   script loads the pubkey DTB and copies it over `fdtcontroladdr` before `bootm`.
   Because the Pi 5 firmware hands U-Boot its own DTB — overriding the built-in
   control DTB — the runtime-injected pubkey is what verification actually uses.

Keep both: the build-time embed is the portable/expected path; the runtime
injection is the Pi-5-specific reason it works in practice.

## CLI surface

```
pqc-boot doctor            # check host prereqs: aarch64 toolchain, ssh reach, API key, deps
pqc-boot migrate           # run the full pipeline (clone → … → verify)
pqc-boot rollback          # restore the backed-up stock boot (undo a deploy/promote) + reboot
pqc-boot generate-patch    # MAINTAINER ONLY: Claude locates RSA → regenerates the pinned diff
```

Config is flags-only (no config file): Pi host/user/ssh-key, U-Boot version
(default pinned `2026.04`), defconfig, keydir, A/B slot, workspace path. API key
from `ANTHROPIC_API_KEY`. `--dry-run` prints commands/transfers without touching
the Pi.

**Low-friction `migrate`:** a bare `pqc-boot migrate` folds in the prereq
check/install (no separate `doctor` run) and, when `--ip` is omitted, prompts
interactively for Pi IP, SSH user, and the Pi **sudo password** (hidden; blank =
passwordless). Flags still drive non-interactive runs; the sudo password then comes
from the `PQCBOOT_SUDO_PASSWORD` env var (never a flag).

**Sudo password handling (security-critical):** held in `Config.sudo_password`
in-memory for the run ONLY — `field(repr=False)` so it can't leak via a logged repr,
and never persisted (`state.py` serializes only completed stages). It reaches the Pi
exclusively via stdin (`ssh._sudo` → `sudo -S -p ''`, password on `in_stream`), NEVER
in the command string/argv (which `ps`/logs can see). `None` → passwordless `sudo -n`.

## Safety & rollback (A/B via Raspberry Pi `tryboot`)

Deploy stages the new boot artifacts to the **tryboot** slot and triggers a
one-shot `tryboot` reboot. The new ML-DSA U-Boot is **promoted to the stable slot
only after `verify` passes**. If verify fails or the Pi is unreachable, the EEPROM
auto-reverts to the known-good RSA boot — no brick. `deploy` always backs up the
existing boot files first.

## Verify

Success = SSH into the rebooted Pi and confirm `/proc/cmdline` contains
`pqc-boot_verified=1` — the flag the boot script bakes into `bootargs` only on the
verified-boot path. On failure the boot script echoes
`pqc-boot: ML-DSA-44 verification failed` and refuses to boot. A negative test
(tampered image is rejected → falls back to RSA boot) proves verification is real,
not cosmetic.

> The PoC used `quboot` branding (`quboot_verified=1`, `QUBOOT:` messages); the
> tool standardizes on **`pqc-boot`** everywhere, so the reconstructed boot script
> and patch must rename those.

## Dev environment & build

- **Host:** WSL Ubuntu (this machine) does the clone, cross-compile, sign, and SSH
  deploy. Assumes a Linux dev host with the `aarch64-linux-gnu-` toolchain.
- **Toolchain/deps checked by `pqc-boot doctor`** before any run.

## Tech stack & conventions

- **CLI:** Typer (type-hint subcommands).
- **Packaging:** `uv` + `pyproject.toml`.
- **SSH/transfer:** Fabric (over Paramiko).
- **Claude:** official `anthropic` SDK. Default model `claude-sonnet-4-6` for the
  build fixer (override to `claude-opus-4-8` for hard cases); model id configurable.
- **External tools** (`git`, cross toolchain): driven via `subprocess`, not reinvented.
- **ML-DSA implementation:** the formally-verified **mldsa-native**
  (pq-code-package), parameter set 44. The tool vendors it from upstream into the
  U-Boot build — it must **not** depend on any local PoC path.
- **Audience/bar:** research/portfolio — reliable on the known Pi 5 setup; happy
  path + A/B safety + verify. Don't over-engineer for arbitrary configs.

## Repo layout

```
pqc_boot/
  cli.py                 # Typer app
  config.py  state.py    # config + resumable pipeline state
  stages/                # clone keys patch build sign deploy verify
  ai/
    build_fixer.py       # runtime Claude touchpoint
    patch_generator.py   # maintainer `generate-patch` touchpoint
  ssh.py  prereqs.py
patches/uboot-2026.04-mldsa44.diff   # the pinned RSA→ML-DSA patch
docs/
  integration.md         # verbatim RSA→ML-DSA-44 integration reference (feeds generate-patch)
tests/
```

## Status

**Done:** scaffold + config/state/context/pipeline; `doctor` (with auto-install);
the Typer `cli.py` (commands: `migrate`, `doctor`, `rollback`, `generate-patch` — no
per-stage commands yet; settings are flags-only, no config file); the reconstructed,
verified pinned patch `patches/uboot-2026.04-mldsa44.diff` (applies to a clean
`v2026.04` and builds host mkimage); the migration reference `docs/integration.md`;
and the **`clone`, `keys`, `patch`, `build`, and `sign` stages** (keys vendors mldsa-native
into `pqc_boot/_mldsa` and compiles a keygen — raw 1312 B pub / 2560 B priv, sign+verify
round-trip proven; patch applies the pinned diff idempotently; build does the two-pass
cross-compile and embeds the pubkey via `fdt_add_pubkey -a sha256,mldsa44 -r conf`,
asserting `algo=sha256,mldsa44` + `required=conf` + 1312 B — proven by a real Pi-5
cross-compile — with the AI build-fixer wired in on failure; sign fetches kernel/dtb/
initramfs off the Pi over SSH, generates the FIT `.its` itself, signs with the built
`mkimage` (no `-K` — build already embedded the pubkey), and self-verifies with
`fit_check_sign`. The signed `.itb` signature node has no `required` prop — fail-closed
enforcement lives only in the control DTB. The SSH-fetch path is unverified pending real
Pi hardware); and the **`deploy` stage** (generates the rebranded `boot.scr`, recomputing
the `cp.b` length = dtb size and `unzip` src = `0x30000000 +` the FIT kernel-data offset
parsed from the FDT — never hardcoded; backs up `config.txt`, stages artifacts as NEW
files, arms Pi one-shot `tryboot`, reboots `"0 tryboot"`. boot.scr gen + derived values
proven; the live SSH/tryboot/reboot path is unverified pending real Pi); the **`verify`
stage** (reconnects after tryboot, asserts `pqc-boot_verified=1` as a whole token in
/proc/cmdline, atomically promotes `tryboot.txt`→`config.txt` via stage+`mv` ONLY on a
positive marker — absent marker / unreachable Pi fails without promoting); and the
**`rollback` command** (`pqc_boot/rollback.py` + `pqc-boot rollback`: restore the
backed-up config.txt, remove tryboot.txt + staged artifacts, reboot to stock). verify
+ rollback logic is unit-tested with a mocked SSH layer; the live SSH/reboot path is
unverified pending real Pi.

**Remaining:** the maintainer `generate-patch` generator (currently reports
not-implemented); per-stage CLI commands; and a real Pi hardware run. The migrate
pipeline is otherwise code-complete.

The PoC U-Boot tree has **no git history** and the local PoC dirs are **read-only
reference only** — never a path the tool depends on. Migration internals — exact RSA
call sites, the mldsa-native vendoring, defconfig, FIT/key specifics — live in
`docs/integration.md`.
