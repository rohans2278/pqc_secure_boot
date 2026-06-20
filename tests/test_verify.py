from pqc_boot.config import VERIFIED_MARKER
from pqc_boot.stages.verify import cmdline_is_verified


def test_marker_present():
    cmdline = f"console=ttyAMA10,115200 root=PARTUUID=29e21ec0-02 rootwait {VERIFIED_MARKER}"
    assert cmdline_is_verified(cmdline)


def test_marker_absent():
    assert not cmdline_is_verified("console=ttyAMA10,115200 root=/dev/mmcblk0p2 rootwait")


def test_marker_must_be_a_whole_token():
    # a substring match must not count as verified
    assert not cmdline_is_verified("xpqc-boot_verified=10")


def test_empty_cmdline_is_not_verified():
    # (a failed READ is handled upstream as SSHCommandError, not passed here as "")
    assert not cmdline_is_verified("")


def test_marker_value_is_pinned():
    assert VERIFIED_MARKER == "pqc-boot_verified=1"
