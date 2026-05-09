"""Tenant registry and repo→tenant ownership.

Phase-1..7 used `repo_id` as the only tenant key; Phase 8 layers a
`tenant_id` above it. A tenant owns N repos. Access checks always
resolve through this mapping so cross-tenant retrieval is impossible
even when two tenants happen to ingest identical data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    name: str
    max_repos: int = 1024
    max_ingestion_bytes_per_day: int = 50 * 1024 * 1024 * 1024  # 50 GiB


@dataclass(frozen=True, slots=True)
class TenantRegistration:
    tenant: Tenant
    repo_ids: tuple[str, ...] = field(default_factory=tuple)


class TenantNotFoundError(LookupError):
    pass


class CrossTenantAccessError(PermissionError):
    pass


class TenantManager:
    """In-memory registry. Persistence is out of scope for Phase 8 —
    this is the source of truth for shape; backing it with Postgres
    is a Phase-9 concern that wouldn't change this contract.
    """

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._repo_to_tenant: dict[str, str] = {}

    # ----- registration -----
    def register_tenant(self, tenant: Tenant) -> None:
        if tenant.tenant_id in self._tenants:
            raise ValueError(f"tenant '{tenant.tenant_id}' already registered")
        self._tenants[tenant.tenant_id] = tenant

    def assign_repo(self, *, tenant_id: str, repo_id: str) -> None:
        if tenant_id not in self._tenants:
            raise TenantNotFoundError(tenant_id)
        existing = self._repo_to_tenant.get(repo_id)
        if existing is not None and existing != tenant_id:
            raise CrossTenantAccessError(
                f"repo '{repo_id}' already owned by tenant '{existing}'"
            )
        self._repo_to_tenant[repo_id] = tenant_id

    # ----- queries -----
    def tenant_for_repo(self, repo_id: str) -> str:
        if repo_id not in self._repo_to_tenant:
            raise TenantNotFoundError(f"repo '{repo_id}' not registered")
        return self._repo_to_tenant[repo_id]

    def get_tenant(self, tenant_id: str) -> Tenant:
        if tenant_id not in self._tenants:
            raise TenantNotFoundError(tenant_id)
        return self._tenants[tenant_id]

    def list_repos(self, tenant_id: str) -> tuple[str, ...]:
        if tenant_id not in self._tenants:
            raise TenantNotFoundError(tenant_id)
        return tuple(sorted(
            r for r, t in self._repo_to_tenant.items() if t == tenant_id
        ))

    def list_tenants(self) -> tuple[Tenant, ...]:
        return tuple(sorted(self._tenants.values(), key=lambda t: t.tenant_id))

    # ----- enforcement -----
    def assert_owns_repo(self, *, tenant_id: str, repo_id: str) -> None:
        owner = self._repo_to_tenant.get(repo_id)
        if owner is None:
            raise TenantNotFoundError(f"repo '{repo_id}' not registered")
        if owner != tenant_id:
            raise CrossTenantAccessError(
                f"tenant '{tenant_id}' may not access repo '{repo_id}' "
                f"(owned by '{owner}')"
            )


__all__ = [
    "CrossTenantAccessError",
    "Tenant",
    "TenantManager",
    "TenantNotFoundError",
    "TenantRegistration",
]
