from core.paper_executor.config import ExecutorConfig
from core.paper_executor.models import Mode
from core.paper_executor.service import PaperExecutorApp


def test_app_restarts_in_shadow_even_if_config_requested_paper(tmp_path):
    cfg = ExecutorConfig(mode=Mode.PAPER, runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite")
    app = PaperExecutorApp(cfg)
    assert app.mode == Mode.SHADOW
