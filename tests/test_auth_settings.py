from core.config import Settings


def test_session_ttl_default():
    s = Settings()
    assert s.session_ttl_seconds == 86400


def test_session_ttl_override(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "3600")
    assert Settings().session_ttl_seconds == 3600
