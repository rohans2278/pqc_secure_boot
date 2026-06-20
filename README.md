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
pqc-boot generate-patch    # MAINTAINER ONLY: regenerate the pinned RSA→ML-DSA patch via Claude
```

Settings come from flags (there is no config file). Common flags on `migrate`:
`--ip <pi-ip>`, `--user <ssh-user>` (default `pi`), `--from <stage>`, `--force`,
`--dry-run`, `--model <claude-model>`. `doctor` takes `--ip` and `--yes`.

`pqc-boot migrate --dry-run` prints what each stage would do without executing.

## Status

Implemented and tested: the CLI, host `doctor`, the pinned patch
`patches/uboot-2026.04-mldsa44.diff` (verified to apply to a clean `v2026.04` and
build host `mkimage`), and the **`clone`** and **`keys`** stages (keys generates the
raw ML-DSA-44 keypair — 1312 B public / 2560 B private — via vendored mldsa-native,
proven with a sign+verify round-trip).

Not yet implemented: the `patch` / `build` / `sign` / `deploy` / `verify` stage
bodies, the `generate-patch` generator (currently reports "not implemented"),
`rollback`, per-stage CLI commands, and a real Pi hardware run. Running `migrate`
today stops at the first unimplemented stage.

See [docs/integration.md](docs/integration.md) for the exact RSA→ML-DSA-44 migration
reference.
