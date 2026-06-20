from pathlib import Path

from pqc_boot.config import Config
from pqc_boot.context import Context


def make_ctx(tmp_path, dry_run=False):
    cfg = Config(workspace=tmp_path)
    return Context.create(cfg, dry_run=dry_run)


def test_derived_paths(tmp_path):
    ctx = make_ctx(tmp_path)
    assert ctx.workspace == tmp_path
    assert ctx.uboot_dir == tmp_path / "u-boot"
    assert ctx.keydir == tmp_path / "keys"


def test_run_executes_when_not_dry(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=False)
    proc = ctx.run(["echo", "hello"])
    assert proc is not None
    assert proc.stdout.strip() == "hello"


def test_run_is_noop_under_dry_run(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    # would fail loudly if actually executed
    proc = ctx.run(["false"])
    assert proc is None
