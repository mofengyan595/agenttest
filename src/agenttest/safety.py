"""Operator-owned target policy. No DNS lookup or redirect can change the destination."""

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoleCredential(StrictModel):
    username_env: str | None = None
    password_env: str | None = None
    bearer_env: str | None = None


class TargetConfig(StrictModel):
    base_url: str = "http://127.0.0.1:8080"
    environment: Literal["local", "isolated"] = "local"
    authorized_ips: list[str] = Field(default_factory=list)
    allow_mutations: bool = False
    allowed_operations: list[str] = Field(min_length=1)
    roles: dict[str, RoleCredential] = Field(default_factory=dict)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_requests: int = Field(default=40, ge=1, le=200)
    max_body_bytes: int = Field(default=100_000, ge=1, le=1_000_000)
    connect_timeout: float = Field(default=2.0, gt=0, le=30)
    read_timeout: float = Field(default=5.0, gt=0, le=30)
    run_timeout: float = Field(default=60.0, gt=0, le=300)
    min_request_interval: float = Field(default=0.01, ge=0, le=5)

    @model_validator(mode="after")
    def validate_target(self) -> "TargetConfig":
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Target must be an HTTP origin without userinfo/path/query")
        _ = parsed.port
        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Stage 1 requires an IP literal or localhost; DNS disabled") from exc
        if not address.is_loopback:
            if (self.environment != "isolated" or str(address) not in self.authorized_ips
                    or not address.is_private or address.is_link_local
                    or address.is_multicast or address.is_unspecified or address.is_reserved):
                raise ValueError("Only explicitly authorized isolated private IPs are allowed")
        if self.allow_mutations and self.environment != "isolated":
            raise ValueError("Write operations require explicitly isolated mode")
        # Pin localhost to an IP before HTTPX sees it, so DNS rebinding is impossible.
        host_part = f"[{address}]" if address.version == 6 else str(address)
        port = f":{parsed.port}" if parsed.port else ""
        self.base_url = f"{parsed.scheme}://{host_part}{port}"
        return self

    def permit(self, operation_id: str, method: str) -> None:
        if operation_id not in self.allowed_operations:
            raise ValueError("Operation is not authorized")
        if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("HTTP method is not allowlisted")
        if method not in {"GET", "HEAD", "OPTIONS"} and not self.allow_mutations:
            raise ValueError("Mutating operation is not authorized")
