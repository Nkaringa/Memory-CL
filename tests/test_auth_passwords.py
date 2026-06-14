from core.auth.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert h.startswith("$argon2")
    assert verify_password("correct horse battery staple", h) is True


def test_wrong_password_fails():
    h = hash_password("s3cret")
    assert verify_password("nope", h) is False


def test_two_hashes_differ_by_salt():
    assert hash_password("same") != hash_password("same")
