from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_VIEWER = "viewer"
ORG_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER)

@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "agent"]
    user_id: str
    org_id: str
    email: str
    roles: tuple[str, ...] = field(default_factory=tuple)
    is_authenticated: bool = False

    def has_role(self, role: str) -> bool:
        return role in self.roles

    @classmethod
    def agent(cls, org_id: str) -> "Principal":
        return cls(kind="agent", user_id="agent", org_id=org_id,
                   email="", roles=("agent",), is_authenticated=True)

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls(kind="user", user_id="", org_id="", email="",
                   roles=(), is_authenticated=False)
