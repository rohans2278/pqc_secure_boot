from pqc_boot.state import PipelineState


def test_roundtrip(tmp_path):
    s = PipelineState.load(tmp_path)
    assert not s.is_done("clone")
    s.mark_done("clone")
    s.mark_done("build")
    # reload from disk
    s2 = PipelineState.load(tmp_path)
    assert s2.is_done("clone")
    assert s2.is_done("build")
    assert s2.completed == ["clone", "build"]


def test_mark_done_idempotent(tmp_path):
    s = PipelineState.load(tmp_path)
    s.mark_done("clone")
    s.mark_done("clone")
    assert s.completed == ["clone"]


def test_reset(tmp_path):
    s = PipelineState.load(tmp_path)
    s.mark_done("clone")
    s.mark_done("build")
    s.reset("clone")
    assert not s.is_done("clone")
    assert s.is_done("build")
    s.reset()
    assert s.completed == []


def test_load_handles_corrupt_file(tmp_path):
    (tmp_path / "state.json").write_text("{ not valid json")
    s = PipelineState.load(tmp_path)
    assert s.completed == []
