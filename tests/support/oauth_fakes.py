"""Fake OAuth clients for testing the OAuth flow without a real provider."""

from __future__ import annotations


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeOAuthClient:
    def __init__(self, *, userinfo=None, github_user=None, github_emails=None):
        self._userinfo = userinfo
        self._github_user = github_user
        self._github_emails = github_emails

    async def authorize_redirect(self, request, redirect_uri):
        from starlette.responses import RedirectResponse
        return RedirectResponse(redirect_uri, status_code=302)

    async def authorize_access_token(self, request):
        return {"userinfo": self._userinfo} if self._userinfo is not None else {}

    async def get(self, path):
        if path == "user":
            return _FakeResp(self._github_user)
        if path == "user/emails":
            return _FakeResp(self._github_emails)
        raise AssertionError(path)


async def install_fake_provider(
    app,
    *,
    provider_id,
    provider_type,
    userinfo=None,
    github_user=None,
    github_emails=None,
    display_name=None,
):
    """Create+enable an auth_providers row AND override the registry so client_for returns a fake."""
    repo = app.state.app_state.auth_provider_repo
    await repo.create(
        id=provider_id,
        provider_type=provider_type,
        display_name=(display_name or provider_type.title()),
        client_id="cid",
        client_secret="sec",
        discovery_url=(
            "https://idp/.well-known/openid-configuration"
            if provider_type == "oidc"
            else None
        ),
        scopes=None,
        enabled=True,
    )
    reg = app.state.oauth_registry
    fake = FakeOAuthClient(
        userinfo=userinfo, github_user=github_user, github_emails=github_emails
    )
    # Monkeypatch the registry instance so client_for(provider_id) -> fake
    # and provider_type_for works. Instance-attribute override works because
    # client_for/provider_type_for are plain instance methods.
    orig_client_for = reg.client_for
    orig_type_for = reg.provider_type_for
    reg.client_for = lambda pid: fake if pid == provider_id else orig_client_for(pid)
    reg.provider_type_for = (
        lambda pid: provider_type if pid == provider_id else orig_type_for(pid)
    )
    return fake
