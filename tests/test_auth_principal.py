from core.auth.principal import Principal, ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER, ORG_ROLES

def test_agent_principal_factory():
    p = Principal.agent(org_id="default")
    assert p.kind == "agent" and p.is_authenticated and p.org_id == "default"
    assert "agent" in p.roles

def test_user_principal_has_role_and_org():
    p = Principal(kind="user", user_id="u1", org_id="acme", email="a@b.c",
                  roles=("admin",), is_authenticated=True)
    assert p.has_role(ROLE_ADMIN) and not p.has_role(ROLE_OWNER)

def test_org_roles_constant():
    assert ORG_ROLES == (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER)
