from core import disk_cache
from core.request_context import ProviderRequestContext, activate, clear


def test_session_scoped_cache_never_writes_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_cache, "CACHE_DIR", tmp_path)
    activate(ProviderRequestContext("user-a", "access-token", "session-a"))
    try:
        disk_cache.put("chain|user-a|session-a", {"secret": "must-not-persist"})
        assert disk_cache.get("chain|user-a|session-a") is None
        assert list(tmp_path.iterdir()) == []
    finally:
        clear()
