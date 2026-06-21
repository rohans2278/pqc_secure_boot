"""rollback: restore the Pi's stock boot, undoing a pqc-boot deploy/promote.

Recovery command (not a pipeline stage). Uses the backup deploy made under
{BACKUP_DIR}: restores the original config.txt, removes the armed tryboot.txt and the
staged ML-DSA artifacts, and reboots the Pi back to stock. Safe to run whether or not
verify promoted — it always returns the stable slot to the backed-up config.

UNVERIFIED ON HARDWARE: the live SSH/reboot path has not been exercised on a real Pi.
The command sequence is unit-tested with a mocked ssh module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import ssh
from .stages import deploy, sign

if TYPE_CHECKING:
    from .context import Context

# Artifacts deploy staged onto the boot partition (removed on rollback).
_STAGED = ("u-boot.bin", deploy.DTB_DEPLOY_NAME, "boot.scr", sign.ITB_NAME)


def plan(ctx: "Context") -> str:
    target = ctx.config.pi_ip or "<pi-ip>"
    return (f"restore stock config.txt from backup on {ctx.config.pi_user}@{target}; "
            "remove tryboot.txt + staged artifacts; reboot to stock")


def run(ctx: "Context") -> None:
    if not ctx.config.pi_ip:
        raise RuntimeError("rollback requires the Pi IP (pass --ip)")

    b = deploy.BOOT_DIR
    backup = f"{deploy.BACKUP_DIR}/config.txt"

    pw = ctx.config.sudo_password
    conn = ssh.connect(ctx.config.pi_ip, ctx.config.pi_user)
    try:
        ssh.sudo_checked(conn, "true", password=pw)  # fail fast if sudo unusable

        if not ssh.run_remote(conn, f"test -f {backup}").ok:
            raise RuntimeError(
                f"no backup found at {backup}; nothing to roll back "
                "(was deploy ever run on this Pi?)"
            )

        # Restore the stock config.txt atomically (stage + mv).
        ssh.sudo_checked(
            conn,
            f"sh -c 'cp {backup} {b}/config.txt.new && mv {b}/config.txt.new {b}/config.txt'",
            password=pw,
        )
        # Remove the armed tryboot config + the staged ML-DSA artifacts.
        staged = " ".join(f"{b}/{name}" for name in _STAGED)
        ssh.sudo_checked(conn, f"rm -f {b}/tryboot.txt {staged}", password=pw)

        ctx.info(f"[dim]$ sudo reboot ({ctx.config.pi_ip})[/dim]")
        ssh.sudo_run(conn, "reboot", password=pw)  # connection drop expected
    finally:
        conn.close()

    ctx.info("[green]✓[/green] restored stock boot from backup and rebooting — "
             "the Pi is back on its original (RSA) boot path")
