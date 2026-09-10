from core.paper_executor.config import ExecutorConfig
from core.paper_executor.models import Mode
from core.paper_executor.service import PaperExecutorApp


def test_app_restarts_in_shadow_even_if_config_requested_paper(tmp_path):
    cfg = ExecutorConfig(mode=Mode.PAPER, runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite")
    app = PaperExecutorApp(cfg)
    assert app.mode == Mode.SHADOW


def test_start_does_not_run_recovery_twice(tmp_path, monkeypatch):
    app = PaperExecutorApp(ExecutorConfig(runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite"))
    monkeypatch.setattr(app.runtime, "recover", lambda: (_ for _ in ()).throw(AssertionError("duplicate recovery")))
    app.runtime.start()
    app.runtime.stop()
