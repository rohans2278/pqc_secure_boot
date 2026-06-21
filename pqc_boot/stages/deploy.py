"""deploy: push the new boot artifacts to the Pi's tryboot (A/B) slot over SSH.

Flow (Raspberry Pi one-shot tryboot — no brick):
  - Generate boot.scr from a tool-owned, rebranded boot.txt template with the two
    per-build DERIVED values computed here, never hardcoded:
      * cp.b length  = size of the pushed u-boot.dtb (pubkey-bearing control DTB);
      * unzip source = 0x30000000 + the kernel-data offset inside the signed FIT
        (parsed from the FIT's FDT struct block; reproduces the PoC's 0x300000f0).
  - Back up the Pi's current config.txt (and any artifacts we'd shadow).
  - Stage the new artifacts as NEW files on /boot/firmware — the stock config.txt is
    left untouched, so the stable boot path is unchanged until promotion.
  - Arm tryboot.txt (= config.txt + kernel=u-boot.bin) and trigger
    `reboot "0 tryboot"`. The firmware boots tryboot.txt ONCE and auto-reverts to
    config.txt on any failure.

verify (next stage) reconnects, asserts pqc-boot_verified=1, PROMOTES
(tryboot.txt -> config.txt), and carries the `rollback` command.

UNVERIFIED ON HARDWARE: the PoC deployed via a manual SD-card mount, not SSH+tryboot,
so this SSH/tryboot/reboot path has not been exercised on a real Pi. The deterministic
parts (boot.scr generation + derived values) are tested; the live Pi flow is not.
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .. import ssh
from ..config import VERIFIED_MARKER
from . import build, sign

if TYPE_CHECKING:
    from ..context import Context

BOOT_DIR = "/boot/firmware"           # Pi 5 / RPi-OS bookworm firmware partition
BACKUP_DIR = f"{BOOT_DIR}/pqcboot-backup"
FIT_LOAD_ADDR = 0x30000000            # where the boot script loads rpi5.itb
DTB_DEPLOY_NAME = "u-boot.dtb"        # boot.txt loads "u-boot.dtb" (the pubkey DTB)
FAIL_MSG = "pqc-boot: ML-DSA-44 verification failed, refusing to boot"

_BOOT_TXT_TEMPLATE = """\
cp.b ${fdtcontroladdr} ${fdt_addr_r} 0x20000
load mmc 0:1 0x10000000 u-boot.dtb
cp.b 0x10000000 ${fdtcontroladdr} @@DTB_LEN@@
load mmc 0:1 0x30000000 rpi5.itb
setenv bootargs "@@BOOTARGS@@"
if bootm start 0x30000000#conf-1; then unzip @@UNZIP_SRC@@ 0x80000; load mmc 0:1 0x10000000 initramfs_2712; booti 0x80000 0x10000000:${filesize} ${fdt_addr_r}; fi
echo "@@FAIL_MSG@@"
"""


def plan(ctx: "Context") -> str:
    target = ctx.config.pi_ip or "<pi-ip>"
    return (f"generate boot.scr; back up boot files on {ctx.config.pi_user}@{target}; "
            f"stage artifacts + arm tryboot; reboot \"0 tryboot\"")


def _kernel_data_offset(itb_path: Path) -> int:
    """Byte offset of /images/kernel/data within a FIT, by walking the FDT struct
    block. Authoritative (reproduces the PoC's 0xf0); no gzip-magic guessing."""
    blob = itb_path.read_bytes()
    (magic, _total, off_struct, off_strings, _off_mem, _ver, _last,
     _boot, _sz_strings, sz_struct) = struct.unpack(">10I", blob[:40])
    if magic != 0xd00dfeed:
        raise RuntimeError(f"{itb_path} is not a FIT/FDT (magic 0x{magic:x})")
    FDT_BEGIN, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
    p = off_struct
    stack: list[str] = []
    end = off_struct + sz_struct
    while p < end:
        (tok,) = struct.unpack(">I", blob[p:p + 4]); p += 4
        if tok == FDT_BEGIN:
            name_end = blob.index(b"\0", p)
            stack.append(blob[p:name_end].decode())
            p = (name_end + 1 + 3) & ~3
        elif tok == FDT_END_NODE:
            stack.pop()
        elif tok == FDT_PROP:
            (length, nameoff) = struct.unpack(">II", blob[p:p + 8]); p += 8
            data_off = p
            ne = blob.index(b"\0", off_strings + nameoff)
            pname = blob[off_strings + nameoff:ne].decode()
            if pname == "data" and stack[-2:] == ["images", "kernel"]:
                return data_off
            p = (p + length + 3) & ~3
        elif tok == FDT_NOP:
            pass
        elif tok == FDT_END:
            break
        else:
            raise RuntimeError(f"bad FDT token {tok} at {p}")
    raise RuntimeError(f"/images/kernel/data not found in {itb_path}")


def render_boot_txt(*, dtb_len: int, unzip_src: int, bootargs: str) -> str:
    """The tool-owned boot script. Bakes the verified marker into bootargs and the
    two per-build derived values; rebranded to pqc-boot (no quboot tokens)."""
    return (_BOOT_TXT_TEMPLATE
            .replace("@@DTB_LEN@@", f"0x{dtb_len:x}")
            .replace("@@UNZIP_SRC@@", f"0x{unzip_src:x}")
            .replace("@@BOOTARGS@@", bootargs)
            .replace("@@FAIL_MSG@@", FAIL_MSG))


def run(ctx: "Context") -> None:
    uboot = ctx.uboot_dir
    mkimage = uboot / "tools" / "mkimage"
    binary = uboot / "u-boot.bin"
    dtb = uboot / build.PUBKEY_DTB
    itb = sign.fit_dir(ctx) / sign.ITB_NAME

    # Preconditions (clear errors before any SSH).
    if not mkimage.is_file():
        raise RuntimeError(f"{mkimage} not found; run the build stage first")
    if not binary.is_file() or not dtb.is_file():
        raise RuntimeError(f"missing build artifacts ({binary.name}/{dtb.name}); "
                           "run the build stage first")
    if not itb.is_file():
        raise RuntimeError(f"signed FIT {itb} not found; run the sign stage first")
    if not ctx.config.pi_ip:
        raise RuntimeError("no Pi IP set; pass --ip to deploy to the Pi")

    # Derived values (computed here, never hardcoded).
    dtb_len = dtb.stat().st_size
    unzip_src = FIT_LOAD_ADDR + _kernel_data_offset(itb)
    ctx.info(f"[dim]derived: cp.b len=0x{dtb_len:x}, unzip src=0x{unzip_src:x}[/dim]")

    conn = ssh.connect(ctx.config.pi_ip, ctx.config.pi_user)
    try:
        # Fail fast if the SSH user lacks passwordless sudo, before doing any work.
        ssh.sudo_checked(conn, "true")

        # bootargs = the Pi's stock kernel cmdline + the verified marker.
        cmdline = ssh.run_checked(conn, f"cat {BOOT_DIR}/cmdline.txt").stdout.strip()
        bootargs = f"{cmdline} {VERIFIED_MARKER}"

        # Generate + compile boot.scr locally with the built mkimage.
        fdir = sign.fit_dir(ctx)
        fdir.mkdir(parents=True, exist_ok=True)
        boot_txt = fdir / "boot.txt"
        boot_scr = fdir / "boot.scr"
        boot_txt.write_text(render_boot_txt(dtb_len=dtb_len, unzip_src=unzip_src,
                                            bootargs=bootargs))
        proc = ctx.run([str(mkimage), "-C", "none", "-A", "arm64", "-T", "script",
                        "-d", str(boot_txt), str(boot_scr)], check=False)
        if proc is not None and proc.returncode != 0:
            raise RuntimeError(f"failed to build boot.scr:\n{proc.stderr.strip()}")

        # Back up the stock config.txt (+ any artifacts we'd shadow). -n keeps the
        # first (pristine) backup intact across re-deploys.
        ssh.sudo_checked(conn, f"mkdir -p {BACKUP_DIR}")
        for name in ("config.txt", "u-boot.bin", DTB_DEPLOY_NAME, "boot.scr",
                     sign.ITB_NAME):
            ssh.sudo_checked(
                conn,
                f"sh -c 'test -f {BOOT_DIR}/{name} && cp -n {BOOT_DIR}/{name} "
                f"{BACKUP_DIR}/{name} || true'",
            )

        # Stage the new artifacts as NEW files (stock config.txt untouched).
        ssh.push_root(conn, str(binary), f"{BOOT_DIR}/u-boot.bin")
        ssh.push_root(conn, str(dtb), f"{BOOT_DIR}/{DTB_DEPLOY_NAME}")
        ssh.push_root(conn, str(boot_scr), f"{BOOT_DIR}/boot.scr")
        ssh.push_root(conn, str(itb), f"{BOOT_DIR}/{sign.ITB_NAME}")

        # Arm tryboot: tryboot.txt = config.txt + kernel=u-boot.bin (+ enable_uart).
        _arm_tryboot(ctx, conn)

        # One-shot tryboot reboot. The connection drops; that's expected.
        ctx.info(f"[dim]$ sudo reboot \"0 tryboot\" ({ctx.config.pi_ip})[/dim]")
        ssh.run_remote(conn, 'sudo -n reboot "0 tryboot"')
    finally:
        conn.close()

    ctx.info("[green]✓[/green] staged tryboot slot + rebooting into tryboot — "
             "run verify to assert pqc-boot_verified=1 and promote")


def _arm_tryboot(ctx: "Context", conn) -> None:
    """Write tryboot.txt = stock config.txt with the U-Boot kernel line added."""
    cfg = ssh.run_checked(conn, f"cat {BOOT_DIR}/config.txt").stdout
    lines = cfg.splitlines()
    for key, line in (("kernel=", "kernel=u-boot.bin"), ("enable_uart=", "enable_uart=1")):
        if not any(l.strip().startswith(key) for l in lines):
            lines.append(line)
    content = "\n".join(lines).rstrip("\n") + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".tryboot", delete=False) as tf:
        tf.write(content)
        tmp = tf.name
    try:
        ssh.push_root(conn, tmp, f"{BOOT_DIR}/tryboot.txt")
    finally:
        Path(tmp).unlink(missing_ok=True)
