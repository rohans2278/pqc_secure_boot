"""deploy: push the new boot artifacts to the Pi's tryboot (A/B) slot over SSH.

Safety invariants:
  - Back up the existing boot files first.
  - Stage to the tryboot slot only; the stable slot is never overwritten before
    verify passes (promotion happens in the verify stage).
  - On deploy's own failure (e.g. transfer dies), clean up the partial tryboot
    artifacts and abort. The stable slot is untouched, so the Pi still boots.
    `pqc-boot rollback` stays a separate, manual command (not auto-invoked here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import Context


def plan(ctx: "Context") -> str:
    target = ctx.config.pi_ip or "<pi-ip>"
    return (f"back up current boot files on {ctx.config.pi_user}@{target}; "
            f"push artifacts to the {ctx.config.slot} slot over SSH")


def run(ctx: "Context") -> None:
    if not ctx.config.pi_ip:
        raise ValueError("deploy requires the Pi IP (pass --ip)")
    raise NotImplementedError(
        "deploy stage not yet implemented: via ssh.py back up boot files and push "
        "the signed FIT + U-Boot to the tryboot slot; on failure clean up the "
        "partial tryboot artifacts and abort (stable slot untouched)"
    )
