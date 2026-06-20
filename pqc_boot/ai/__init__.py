"""Claude API touchpoints — the only non-deterministic parts of pqc-boot.

Two call sites, both here so the Anthropic surface is easy to audit:
  - build_fixer:     runtime, self-correct U-Boot build failures (end-user path)
  - patch_generator: maintainer-only, (re)generate the pinned RSA->ML-DSA patch

The `anthropic` SDK is imported lazily inside functions so the rest of the tool
runs without it installed.
"""
