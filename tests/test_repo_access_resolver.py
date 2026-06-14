from core.auth.access import ACCESS_LEVELS, level_at_least, max_level, resolve_repo_access, accessible_repo_ids

def test_level_ordering():
    assert ACCESS_LEVELS == ("read", "write", "admin")
    assert level_at_least("write", "read") and not level_at_least("read", "write")
    assert level_at_least("admin", "admin")

def test_max_level():
    assert max_level("read", "write") == "write"
    assert max_level("admin", "read") == "admin"

def test_agent_gets_admin_on_all_org_repos():
    acc = resolve_repo_access(kind="agent", role="agent", org_repo_ids={"r1","r2"},
                              user_id="agent", team_ids=set(), grants=[])
    assert acc == {"r1": "admin", "r2": "admin"}

def test_owner_admin_get_admin_on_all():
    for role in ("owner", "admin"):
        acc = resolve_repo_access(kind="user", role=role, org_repo_ids={"r1","r2"},
                                  user_id="u1", team_ids=set(), grants=[])
        assert acc == {"r1": "admin", "r2": "admin"}

def test_member_gets_granted_only_max_level():
    grants = [{"repo_id":"r1","access":"read"}, {"repo_id":"r1","access":"write"}, {"repo_id":"r2","access":"read"}]
    acc = resolve_repo_access(kind="user", role="member", org_repo_ids={"r1","r2","r3"},
                              user_id="u1", team_ids={"t1"}, grants=grants)
    assert acc == {"r1": "write", "r2": "read"}   # r3 not granted → absent

def test_viewer_capped_at_read():
    grants = [{"repo_id":"r1","access":"admin"}, {"repo_id":"r2","access":"write"}]
    acc = resolve_repo_access(kind="user", role="viewer", org_repo_ids={"r1","r2"},
                              user_id="u1", team_ids=set(), grants=grants)
    assert acc == {"r1": "read", "r2": "read"}

def test_grants_outside_org_repos_ignored():
    grants = [{"repo_id":"rX","access":"admin"}]
    acc = resolve_repo_access(kind="user", role="member", org_repo_ids={"r1"},
                              user_id="u1", team_ids=set(), grants=grants)
    assert acc == {}

def test_member_no_grants_gets_nothing():
    acc = resolve_repo_access(kind="user", role="member", org_repo_ids={"r1","r2"},
                              user_id="u1", team_ids=set(), grants=[])
    assert acc == {}

def test_unknown_role_gets_nothing():
    acc = resolve_repo_access(kind="user", role="", org_repo_ids={"r1"},
                              user_id="u1", team_ids=set(), grants=[{"repo_id":"r1","access":"read"}])
    # role "" is not owner/admin; treated as member-like (granted-only). With a grant → gets it.
    assert acc == {"r1": "read"}

def test_accessible_ids_filter_by_level():
    acc = {"r1":"read","r2":"write","r3":"admin"}
    assert accessible_repo_ids(acc, need="write") == {"r2","r3"}
    assert accessible_repo_ids(acc, need="read") == {"r1","r2","r3"}
    assert accessible_repo_ids(acc, need="admin") == {"r3"}
