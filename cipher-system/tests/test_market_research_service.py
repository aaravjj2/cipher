from pathlib import Path


# The checkout root is two levels above this file (`cipher-system/tests`). Using
# parents[3] only worked in the local symlinked workspace, where it resolved to
# a sibling directory that happened to contain infra/.
ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "infra/gcp-cipher-vm/systemd/cipher-market-research.service"
TIMER = ROOT / "infra/gcp-cipher-vm/systemd/cipher-market-research.timer"


def test_research_service_is_scheduled_but_has_no_order_authority():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    assert "run_market_research_agent.py" in service
    assert "ReadWritePaths=/home/aarav/Aarav/cipher/cipher-system/data/research_agent" in service
    assert timer.count("OnCalendar=") == 4
    assert "Mon..Fri" in timer
    combined = (service + timer).lower()
    assert "/v2/orders" not in combined
    assert "submit_order" not in combined
