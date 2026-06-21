# pqc-boot

A Python CLI that migrates a **Raspberry Pi 5**'s boot chain from RSA to
post-quantum **ML-DSA-44** verification. It runs the deterministic steps itself —
clone U-Boot `v2026.04`, cross-compile, ML-DSA-sign, deploy over SSH — and calls the
**Claude API** only where rote automation can't. You need an `ANTHROPIC_API_KEY`, not
a Claude Code account.

## Trust boundary (what it actually changes)

- **In scope:** U-Boot verifies the next stage (the kernel **FIT** image) with
  **ML-DSA-44** instead of RSA.
- **Out of scope:** the Broadcom EEPROM bootloader is RSA-signed by Raspberry Pi and
  not user-replaceable — it remains the RSA root of trust. pqc-boot does **not** make
  the whole chain quantum-safe.

## Requirements

- A Linux dev host (developed on WSL Ubuntu) with the `aarch64-linux-gnu-` toolchain,
  `git`, `make`, `mkimage`, `dtc` — `pqc-boot doctor` checks these and offers to
  install what's missing.
- `ANTHROPIC_API_KEY` in the environment (used by the build self-correction touchpoint).
- A reachable Raspberry Pi 5 over SSH (for the deploy/verify stages).

## Install

```sh
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
```

This installs the `pqc-boot` console script and the dev/test deps.

## Commands

```
pqc-boot doctor            # check host prereqs (toolchain, deps, API key) + install missing
pqc-boot migrate           # run the pipeline: clone → keys → patch → build → sign → deploy → verify
pqc-boot rollback          # restore the Pi's stock boot from backup (undo a deploy/promote) + reboot
pqc-boot generate-patch    # MAINTAINER ONLY: regenerate the pinned RSA→ML-DSA patch via Claude
```

Just run `pqc-boot migrate`: it runs the prerequisite check/install for you (no separate
`doctor` step), and if you didn't pass `--ip` it prompts for the **Pi IP**, **SSH user**
(default `pi`), and the **Pi sudo password** (entered hidden; leave blank if the Pi has
passwordless sudo). The sudo password is held in memory for the run only — never written
to disk, never logged, and passed to `sudo` via stdin (`sudo -S`), never on the command
line.

Settings can also come from flags for non-interactive use (there is no config file):
`--ip <pi-ip>`, `--user <ssh-user>`, `--from <stage>`, `--force`, `--dry-run`,
`--model <claude-model>`. When `--ip` is given (non-interactive), supply the sudo password
via the `PQCBOOT_SUDO_PASSWORD` env var (never a flag, so it can't leak into `ps`/history);
omit it to assume passwordless sudo. `doctor` still exists standalone (`--ip`, `--yes`).

`pqc-boot migrate --dry-run` prints what each stage would do without executing (and
without prompting).

## Status

Implemented and tested: the CLI, host `doctor`, the pinned patch
`patches/uboot-2026.04-mldsa44.diff` (verified to apply to a clean `v2026.04` and
build host `mkimage`), and the **`clone`**, **`keys`**, **`patch`**, **`build`**, and
**`sign`** stages. `keys` generates the raw ML-DSA-44 keypair (1312 B public / 2560 B
private) via vendored mldsa-native, proven with a sign+verify round-trip; `patch`
applies the pinned diff idempotently; `build` does the two-pass cross-compile, embeds
the pubkey into the control DTB (`algo=sha256,mldsa44`, `required=conf`, 1312 B —
verified by a real Pi-5 cross-compile), and on a build failure invokes the **AI
build-fixer** (screens every proposed diff, hard-rejects crypto/verify paths, and
applies only after you confirm); `sign` fetches the kernel/dtb/initramfs off the Pi
over SSH, generates the FIT `.its` itself, signs with the built `mkimage`, and
self-verifies the signature with `fit_check_sign` (sign + verify proven locally; the
SSH fetch path is unverified pending real-Pi hardware); `deploy` generates the
rebranded `boot.scr` (recomputing the `cp.b` length and `unzip` offset per build —
no hardcoded addresses), backs up the Pi's `config.txt`, stages the artifacts as new
files, arms Raspberry Pi one-shot `tryboot`, and reboots (boot.scr generation +
derived values proven; the live SSH/tryboot/reboot path is unverified pending a real
Pi); `verify` reconnects after the tryboot reboot, asserts `pqc-boot_verified=1` is
present as a whole token in `/proc/cmdline`, and only then atomically promotes
`tryboot.txt`→`config.txt` (stage-and-`mv`); an absent marker or an unreachable Pi
fails without promoting (the firmware has already reverted to stock). `rollback`
restores the backed-up `config.txt`, removes the staged artifacts, and reboots to
stock. The verify/rollback live SSH paths are likewise unverified pending real-Pi
hardware (the decision/command logic is unit-tested with a mocked SSH layer).

The maintainer **`generate-patch`** generator is also implemented: it clones a clean
`v2026.04`, vendors the mldsa-native core + the 5 U-Boot wrapper files deterministically,
uses Claude only to locate the 6 RSA insertion points (each screened to an allowlist of
exactly that file), then emits the diff and verifies it applies to a fresh tree and
builds host `mkimage` before adopting it. It reproduces the **v2026.04** patch (proven
end-to-end, 46-file footprint) — it is not yet validated against other U-Boot versions.

Not yet implemented: per-stage CLI commands and a real Pi hardware boot. Everything
else is code-complete.

See [docs/integration.md](docs/integration.md) for the exact RSA→ML-DSA-44 migration
reference.
