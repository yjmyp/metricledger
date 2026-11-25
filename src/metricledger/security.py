from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from .models import Principal


@dataclass(frozen=True)
class ApiIdentity:
    """Identity attached to a token by trusted server configuration."""

    principal_id: str
    tenant_id: str
    roles: frozenset[str]
    allowed_values: dict[str, list[str]]

    def as_principal(self) -> Principal:
        return Principal(
            principal_id=self.principal_id,
            tenant_id=self.tenant_id,
            roles=sorted(self.roles),
            allowed_values={
                key: list(values)
                for key, values in self.allowed_values.items()
            },
            auth_policy_version="api-key-v1",
        )


class ApiKeyAuthenticator:
    """Authenticate bearer tokens without retaining raw tokens after config load."""

    def __init__(self, identities_by_digest: Mapping[str, ApiIdentity]):
        self._identities_by_digest = dict(identities_by_digest)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Mapping]) -> ApiKeyAuthenticator:
        identities: dict[str, ApiIdentity] = {}
        for token, raw in mapping.items():
            if not isinstance(token, str) or not token:
                raise ValueError("API keys must be non-empty strings")
            payload = dict(raw)
            tenant_id = str(payload.get("tenant_id", "")).strip()
            principal_id = str(payload.get("principal_id", "")).strip()
            if not tenant_id or not principal_id:
                raise ValueError(
                    "each API key requires tenant_id and principal_id"
                )
            roles = frozenset(str(role) for role in payload.get("roles", []))
            allowed_values = {
                str(key): [str(value) for value in values]
                for key, values in dict(payload.get("allowed_values", {})).items()
            }
            if not allowed_values:
                raise ValueError("each API key requires allowed_values")
            identities[_digest(token)] = ApiIdentity(
                principal_id=principal_id,
                tenant_id=tenant_id,
                roles=roles,
                allowed_values=allowed_values,
            )
        return cls(identities)

    def authenticate(self, authorization: str | None) -> ApiIdentity | None:
        if not authorization:
            return None
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return None
        return self._identities_by_digest.get(_digest(token.strip()))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
