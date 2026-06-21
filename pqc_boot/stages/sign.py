"""sign: ML-DSA-44 sign the kernel FIT image.

Mirrors the proven PoC FIT: kernel + fdt + ramdisk, configuration signature algo
`sha256,mldsa44`, key-name-hint `<keyname>`, signed with the private key via the
freshly built host mkimage. The three FIT inputs are fetched off the running Pi's
boot partition over SSH; the .its is generated here (tool-owned, security-critical),
never taken from the user. The result is verified locally with fit_check_sign against
the pubkey-bearing control DTB the build stage produced.

Enforcement note: the proven signed .itb's signature node carries NO `required`
property — fail-closed enforcement lives entirely in the control DTB
(`required=conf`, set by build's `fdt_add_pubkey -r conf`). So mkimage is run without
`-r` here, matching the proven FIT.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import ssh
from . import build

if TYPE_CHECKING:
    from ..context import Context

ALGO = "sha256,mldsa44"

# FIT input filenames (must match the /incbin references in the generated .its).
KERNEL = "kernel_2712.img"
FDT = "bcm2712-rpi-5-b.dtb"
RAMDISK = "initramfs_2712"
FIT_INPUTS = (KERNEL, FDT, RAMDISK)

ITS_NAME = "rpi5.its"
ITB_NAME = "rpi5.itb"

# Where the inputs live on the Pi. UNVERIFIED against real hardware — best-guess for
# Raspberry Pi OS (bookworm), where the firmware partition mounts at /boot/firmware
# (older layouts use /boot). Confirm on a real Pi.
PI_BOOT_DIRS = ("/boot/firmware", "/boot")


def plan(ctx: "Context") -> str:
    return ("sign FIT (sha256,mldsa44 over kernel+fdt+ramdisk, key-name-hint "
            f"{ctx.config.keyname}) with the ML-DSA-44 private key; inputs fetched "
            "from the Pi, verified with fit_check_sign")


def fit_dir(ctx: "Context") -> Path:
    """Workspace dir holding the fetched inputs, generated .its, and signed .itb."""
    return ctx.workspace / "fit"


def its_text(keyname: str) -> str:
    """The FIT source. Only the key-name-hint varies; algo is fixed sha256,mldsa44.

    Kernel is gzip-compressed (the Pi 5 kernel ships gzipped), loaded/entered at
    0x80000; fdt and ramdisk are uncompressed. conf-1 is the default configuration
    and carries the signature over all three images.
    """
    return f"""/dts-v1/;
/ {{
        description = "Raspberry Pi 5 FIT image with ML-DSA-44 signature";
        #address-cells = <1>;
        images {{
                kernel {{
                        description = "Linux kernel for Raspberry Pi 5";
                        data = /incbin/("{KERNEL}");
                        type = "kernel";
                        arch = "arm64";
                        os = "linux";
                        compression = "gzip";
                        load = <0x00080000>;
                        entry = <0x00080000>;
                        hash-1 {{
                                algo = "sha256";
                        }};
                }};
                fdt {{
                        description = "Raspberry Pi 5 device tree";
                        data = /incbin/("{FDT}");
                        type = "flat_dt";
                        arch = "arm64";
                        compression = "none";
                        hash-1 {{
                                algo = "sha256";
                        }};
                }};
                ramdisk {{
                        description = "Raspberry Pi 5 initramfs";
                        data = /incbin/("{RAMDISK}");
                        type = "ramdisk";
                        arch = "arm64";
                        os = "linux";
                        compression = "none";
                        hash-1 {{
                                algo = "sha256";
                        }};
                }};
        }};
        configurations {{
                default = "conf-1";
                conf-1 {{
                        description = "Raspberry Pi 5 ML-DSA-44 verified boot";
                        kernel = "kernel";
                        fdt = "fdt";
                        ramdisk = "ramdisk";
                        signature-1 {{
                                algo = "{ALGO}";
                                key-name-hint = "{keyname}";
                                sign-images = "kernel", "fdt", "ramdisk";
                        }};
                }};
        }};
}};
"""


def run(ctx: "Context") -> None:
    uboot = ctx.uboot_dir
    mkimage = uboot / "tools" / "mkimage"
    fit_check_sign = uboot / "tools" / "fit_check_sign"
    control_dtb = uboot / build.PUBKEY_DTB
    priv = ctx.keydir / f"{ctx.config.keyname}.bin"

    # Preconditions (clear errors before any SSH).
    for tool in (mkimage, fit_check_sign):
        if not tool.is_file():
            raise RuntimeError(f"{tool} not found; run the build stage first")
    if not control_dtb.is_file():
        raise RuntimeError(f"{control_dtb} not found; run the build stage first")
    if not priv.is_file():
        raise RuntimeError(f"private key {priv} not found; run the keys stage first")
    if not ctx.config.pi_ip:
        raise RuntimeError("no Pi IP set; pass --ip to fetch the FIT inputs from the Pi")

    fdir = fit_dir(ctx)
    fdir.mkdir(parents=True, exist_ok=True)

    _fetch_inputs(ctx, fdir)

    # Generate the .its (tool-owned) next to the fetched inputs.
    its = fdir / ITS_NAME
    its.write_text(its_text(ctx.config.keyname))

    # Sign: cwd = fit dir so the /incbin relative paths resolve. mkimage reads the
    # private key (<keyname>.bin) from the keydir and writes the signature in place.
    itb = fdir / ITB_NAME
    proc = ctx.run([str(mkimage), "-f", ITS_NAME, "-k", str(ctx.keydir), ITB_NAME],
                   cwd=fdir, check=False)
    if proc is not None and proc.returncode != 0:
        raise RuntimeError(f"mkimage failed to sign the FIT:\n{proc.stderr.strip()}")
    if not itb.is_file():
        raise RuntimeError(f"signing finished but {itb} is missing")

    # Self-verify: the signature must verify against the embedded pubkey.
    check = ctx.run([str(fit_check_sign), "-f", str(itb), "-k", str(control_dtb)],
                    check=False)
    if check is not None and check.returncode != 0:
        raise RuntimeError(
            "fit_check_sign rejected the signed FIT (signature does not verify "
            f"against {control_dtb.name}):\n{(check.stderr or check.stdout).strip()}"
        )

    size = itb.stat().st_size
    ctx.info(f"[green]✓[/green] signed {itb.name} "
             f"({size} B; sha256,mldsa44, key-name-hint {ctx.config.keyname}) "
             "— fit_check_sign verified")


def _fetch_inputs(ctx: "Context", dest: Path) -> None:
    """Fetch the three FIT inputs from the Pi's boot partition over SSH."""
    conn = ssh.connect(ctx.config.pi_ip, ctx.config.pi_user)
    try:
        for name in FIT_INPUTS:
            remote = _remote_path(conn, name)
            ctx.info(f"[dim]fetch {ctx.config.pi_user}@{ctx.config.pi_ip}:{remote}[/dim]")
            ssh.fetch(conn, remote, str(dest / name))
    finally:
        conn.close()


def _remote_path(conn, name: str) -> str:
    """Find a FIT input on the Pi, trying /boot/firmware then /boot."""
    for d in PI_BOOT_DIRS:
        candidate = f"{d}/{name}"
        if ssh.run_remote(conn, f"test -f {candidate}").ok:
            return candidate
    raise RuntimeError(
        f"{name} not found on the Pi under {', '.join(PI_BOOT_DIRS)}"
    )
