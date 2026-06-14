from core.auth.providers import PRESETS, PROVIDER_TYPES, build_register_kwargs


def test_known_presets_exist():
    assert {"github", "google", "microsoft", "oidc"} <= set(PROVIDER_TYPES)


def test_google_uses_discovery():
    kw = build_register_kwargs(provider_type="google", client_id="cid", client_secret="sec", discovery_url=None, scopes=None)
    assert kw["server_metadata_url"].startswith("https://accounts.google.com")
    assert "openid" in kw["client_kwargs"]["scope"]
    assert kw["client_kwargs"]["code_challenge_method"] == "S256"


def test_github_is_oauth_not_oidc():
    kw = build_register_kwargs(provider_type="github", client_id="cid", client_secret="sec", discovery_url=None, scopes=None)
    assert "server_metadata_url" not in kw
    assert kw["access_token_url"].startswith("https://github.com")
    assert "user:email" in kw["client_kwargs"]["scope"]


def test_generic_oidc_requires_discovery_url():
    kw = build_register_kwargs(provider_type="oidc", client_id="c", client_secret="s", discovery_url="https://idp.example/.well-known/openid-configuration", scopes="openid email")
    assert kw["server_metadata_url"] == "https://idp.example/.well-known/openid-configuration"


def test_generic_oidc_without_discovery_raises():
    import pytest
    with pytest.raises(ValueError):
        build_register_kwargs(provider_type="oidc", client_id="c", client_secret="s", discovery_url=None, scopes=None)


def test_unknown_type_raises():
    import pytest
    with pytest.raises(ValueError):
        build_register_kwargs(provider_type="bogus", client_id="c", client_secret="s", discovery_url=None, scopes=None)
