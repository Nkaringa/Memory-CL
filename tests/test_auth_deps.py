from apps.api.auth_deps import hash_session_token, new_session_token, COOKIE_NAME


def test_hash_is_deterministic_and_hex():
    t = "raw-token"
    assert hash_session_token(t) == hash_session_token(t)
    assert len(hash_session_token(t)) == 64


def test_new_token_unique():
    assert new_session_token() != new_session_token()


def test_cookie_name():
    assert COOKIE_NAME == "memcl_session"
