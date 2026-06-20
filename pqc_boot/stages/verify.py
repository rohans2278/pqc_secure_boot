"""verify: confirm the ML-DSA boot worked, then promote.

Triggers the one-shot tryboot reboot, reconnects over SSH, and asserts
/proc/cmdline contains the verified marker (which the boot script bakes into
bootargs only on the verified-boot path). Only on a confirmed-verified boot is the
tryboot image promoted to the stable slot. On failure the Pi auto-reverts to RSA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import VERIFIED_MARKER

if TYPE_CHECKING:
    from ..context import Context

# The Pi is unreachable for ~20-60s during the tryboot reboot. Poll for it to come
# back; if it never does within the timeout, the new boot failed and tryboot has
# already reverted to the stable RSA slot -> verify fails (no promotion).
RECONNECT_TIMEOUT_S = 120
RECONNECT_POLL_S = 5


def cmdline_is_verified(cmdline: str) -> bool:
    """True iff the booted kernel's cmdline carries the verified marker.

    The caller must pass only a cmdline that was read successfully. A failed read
    (ssh.read_cmdline raises SSHCommandError) must be treated as 'unknown' and must
    NOT be funneled here as an empty string — unknown is not 'not verified'.
    """
    return VERIFIED_MARKER in cmdline.split()


def plan(ctx: "Context") -> str:
    target = ctx.config.pi_ip or "<pi-ip>"
    return (f"tryboot-reboot {ctx.config.pi_user}@{target}; assert "
            f"'{VERIFIED_MARKER}' in /proc/cmdline; promote tryboot -> stable")


def run(ctx: "Context") -> None:
    if not ctx.config.pi_ip:
        raise ValueError("verify requires the Pi IP (pass --ip)")
    raise NotImplementedError(
        "verify stage not yet implemented. Contract:\n"
        "  1. trigger the one-shot tryboot reboot.\n"
        f"  2. reconnect with a bounded retry loop (poll ~{RECONNECT_POLL_S}s up to "
        f"{RECONNECT_TIMEOUT_S}s): SSH-down means 'not back yet' (keep waiting); "
        "exceeding the timeout means the boot failed and tryboot already reverted to "
        "RSA -> verify FAILS, no promotion.\n"
        "  3. once reachable, read /proc/cmdline via ssh.read_cmdline (raises "
        "SSHCommandError on a failed read).\n"
        "  4. promote tryboot -> stable ONLY when cmdline_is_verified(cmdline) is True "
        "from a successful read. On SSHCommandError do NOT promote (unknown != verified)."
    )
