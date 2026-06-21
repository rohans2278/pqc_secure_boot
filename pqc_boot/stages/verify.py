"""verify: confirm the ML-DSA boot worked, then promote.

deploy already issued `reboot "0 tryboot"`, so verify only RECONNECTS: it waits for
the Pi to come back, asserts /proc/cmdline contains the verified marker (which the
boot script bakes into bootargs only on the ML-DSA verified-boot path), and PROMOTES
the tryboot config to stable (tryboot.txt -> config.txt) so the ML-DSA boot becomes
permanent. If the marker is absent or the Pi never returns, the firmware has already
auto-reverted to the stock config.txt (stable slot safe) -> verify fails, no promotion;
cleanup is a manual `pqc-boot rollback`.

UNVERIFIED ON HARDWARE: the live SSH/reboot/promote path has not been exercised on a
real Pi (the PoC used a manual SD-card flow). The marker check and promote command
sequence are unit-tested with a mocked ssh module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .. import ssh
from ..config import VERIFIED_MARKER
from . import deploy

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
    return (f"reconnect to {ctx.config.pi_user}@{target} after tryboot; assert "
            f"'{VERIFIED_MARKER}' in /proc/cmdline; promote tryboot -> stable")


def _wait_for_pi(ctx: "Context"):
    """Poll until the Pi answers SSH, or fail after RECONNECT_TIMEOUT_S.

    Returns a live connection. An unreachable Pi (connection refused/timeout/no route)
    means 'not back yet' -> keep polling. Exceeding the timeout means the tryboot boot
    failed and the firmware reverted to stock -> verify fails.
    """
    deadline = time.monotonic() + RECONNECT_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        conn = ssh.connect(ctx.config.pi_ip, ctx.config.pi_user)
        try:
            if ssh.run_remote(conn, "true").ok:
                return conn
            conn.close()
        except Exception as e:  # not back yet: connection refused/timeout/no route
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
        ctx.info(f"[dim]waiting for {ctx.config.pi_ip} to come back…[/dim]")
        time.sleep(RECONNECT_POLL_S)
    raise RuntimeError(
        f"Pi {ctx.config.pi_ip} did not return within {RECONNECT_TIMEOUT_S}s after "
        "tryboot — the ML-DSA boot failed and the firmware reverted to the stock slot "
        f"(stable boot intact). Not verified.{f' Last error: {last_err}' if last_err else ''}"
    )


def run(ctx: "Context") -> None:
    if not ctx.config.pi_ip:
        raise RuntimeError("verify requires the Pi IP (pass --ip)")

    conn = _wait_for_pi(ctx)
    try:
        cmdline = ssh.read_cmdline(conn)  # raises SSHCommandError on a failed read
        if not cmdline_is_verified(cmdline):
            raise RuntimeError(
                f"verification failed: '{VERIFIED_MARKER}' not in /proc/cmdline — the "
                "Pi booted the stock slot (tryboot auto-reverted). Stable boot intact; "
                "run `pqc-boot rollback` to clean up the staged tryboot files."
            )
        # Verified: make the ML-DSA boot permanent. This is the single irreversible
        # write to the stable slot, so do it atomically — stage to config.txt.new then
        # `mv` (atomic on one filesystem) so a mid-write failure can't half-write
        # config.txt and brick the stable boot.
        b = deploy.BOOT_DIR
        ssh.sudo_checked(
            conn,
            f"sh -c 'cp {b}/tryboot.txt {b}/config.txt.new && "
            f"mv {b}/config.txt.new {b}/config.txt'",
        )
    finally:
        conn.close()

    ctx.info(f"[green]✓[/green] verified ({VERIFIED_MARKER}) and promoted tryboot → "
             "stable — the Pi now boots ML-DSA-44 by default")
