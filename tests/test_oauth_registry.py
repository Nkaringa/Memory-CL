from core.auth.oauth_registry import OAuthRegistry
from storage.auth_provider_repo import AuthProviderRow


def _row(id, t, enabled=True):
    return AuthProviderRow(id=id, provider_type=t, display_name=t.title(), client_id="cid", client_secret="sec",
                           discovery_url=("https://idp/.well-known/openid-configuration" if t == "oidc" else None),
                           scopes=None, enabled=enabled, created_at=None, updated_at=None)

def test_rebuild_registers_enabled_only():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "google"), _row("p2", "github", enabled=False)])
    assert reg.client_for("p1") is not None
    assert reg.client_for("p2") is None   # disabled not registered
    assert reg.client_for("nope") is None

def test_public_list_masks_secrets():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "google")])
    pub = reg.enabled_public_list()
    assert pub == [{"id": "p1", "provider_type": "google", "display_name": "Google"}]
    assert all("client_secret" not in p and "client_id" not in p for p in pub)

def test_provider_type_for():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "github")])
    assert reg.provider_type_for("p1") == "github"
    assert reg.provider_type_for("nope") is None

def test_rebuild_replaces_previous():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "google")])
    reg.rebuild([_row("p2", "github")])   # p1 gone, p2 present
    assert reg.client_for("p1") is None
    assert reg.client_for("p2") is not None
    assert [p["id"] for p in reg.enabled_public_list()] == ["p2"]
