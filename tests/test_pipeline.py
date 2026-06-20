import pytest

from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot import pipeline
from pqc_boot.pipeline import STAGE_NAMES, get_stage, run_pipeline, run_stage


def make_ctx(tmp_path, dry_run=False):
    return Context.create(Config(workspace=tmp_path), dry_run=dry_run)


def test_stage_order():
    assert STAGE_NAMES == ["clone", "keys", "patch", "build", "sign", "deploy", "verify"]


def test_every_stage_has_plan_and_run():
    for name in STAGE_NAMES:
        mod = get_stage(name)
        assert callable(mod.plan)
        assert callable(mod.run)


def test_get_unknown_stage_raises():
    with pytest.raises(ValueError):
        get_stage("nope")


def test_dry_run_plans_all_stages_without_executing(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    # dry-run must not raise even though most stages are NotImplemented stubs
    run_pipeline(ctx)
    # nothing should be marked done in dry-run
    assert ctx.state.completed == []


def test_plan_strings_render(tmp_path):
    ctx = make_ctx(tmp_path)
    for name in STAGE_NAMES:
        assert isinstance(get_stage(name).plan(ctx), str)


def test_run_stage_marks_done(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    # stub clone.run so we don't hit the network
    monkeypatch.setattr(pipeline.clone, "run", lambda c: None)
    run_stage(ctx, "clone")
    assert ctx.state.is_done("clone")


def test_already_done_is_skipped(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    calls = []
    monkeypatch.setattr(pipeline.clone, "run", lambda c: calls.append(1))
    ctx.state.mark_done("clone")
    run_stage(ctx, "clone")
    assert calls == []  # skipped because already done
