from __future__ import annotations

ACCESS_LEVELS: tuple[str, ...] = ("read", "write", "admin")
_RANK: dict[str, int] = {lvl: i for i, lvl in enumerate(ACCESS_LEVELS)}


def level_at_least(have: str, need: str) -> bool:
    return _RANK[have] >= _RANK[need]


def max_level(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def resolve_repo_access(
    *,
    kind: str,
    role: str,
    org_repo_ids: set[str],
    user_id: str,
    team_ids: set[str],
    grants: list[dict],
) -> dict[str, str]:
    """Return {repo_id: access_level} for repos this principal may access.

    Agents and org owners/admins receive admin on every org repo.
    All other roles (member, viewer, unknown) are limited to explicitly
    granted repos; viewers are further capped at read.
    Grants referencing repos outside org_repo_ids are silently ignored.
    """
    if kind == "agent" or role in ("owner", "admin"):
        return {rid: "admin" for rid in org_repo_ids}

    # Granted-only path: member / viewer / unknown role
    acc: dict[str, str] = {}
    for grant in grants:
        rid = grant["repo_id"]
        if rid not in org_repo_ids:
            continue
        lvl = grant["access"]
        acc[rid] = max_level(acc[rid], lvl) if rid in acc else lvl

    if role == "viewer":
        return {rid: "read" for rid in acc}

    return acc


def accessible_repo_ids(access: dict[str, str], *, need: str = "read") -> set[str]:
    """Filter an access map to repo IDs at or above the required level."""
    return {rid for rid, lvl in access.items() if level_at_least(lvl, need)}
