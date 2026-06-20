from pathlib import Path

from pqc_boot.config import Config, UBOOT_TAG


def test_defaults():
    c = Config()
    assert c.pi_ip is None
    assert c.pi_user == "pi"
    assert c.uboot_tag == UBOOT_TAG == "v2026.04"
    assert c.model == "claude-sonnet-4-6"
    assert c.build_fix_attempts == 3


def test_keydir_is_fixed_under_workspace():
    c = Config(workspace=Path("/tmp/ws"))
    assert c.keydir == Path("/tmp/ws/keys")
    assert c.keyname == "mykey"


def test_uboot_tag_is_hard_pinned():
    # No resolution logic exists; the tag is a constant.
    assert Config().uboot_tag == "v2026.04"
