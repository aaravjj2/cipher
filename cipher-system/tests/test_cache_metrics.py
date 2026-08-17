from core import app


def test_cache_hit_accounting_is_safe_while_cache_lock_is_held():
    before = app._CACHE_METRICS["quote"]["hits"]
    with app._CACHE_LOCK:
        app._cache_event("quote", True)
    assert app._CACHE_METRICS["quote"]["hits"] == before + 1
