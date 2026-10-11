"""Per-run authentication vault; no secret is exported into step binding state."""

import re
from typing import Any

from agenttest.safety import TargetConfig
from agenttest.selectors import select

SENSITIVE = re.compile(
    r"password|passwd|token|secret|authorization|cookie|api.?key|otp|verification.?code|"
    r"pin|identity|id.?card|national.?id|phone|mobile|email|ssn|passport|birth|address|"
    r"account|user.?id|credit.?card|cvv|身份证|验证码|密码", re.I,
)


def sensitive_field(name: str, custom: list[str] = ()) -> bool:
    return bool(SENSITIVE.search(name)) or any(item.lower() in name.lower() for item in custom)


class CredentialVault:
    def __init__(self, config: TargetConfig, environ: dict[str, str]) -> None:
        self.config = config
        self._environ = environ
        self._tokens: dict[str, str] = {}
        self._secrets = {
            environ[name] for role in config.roles.values()
            for name in (role.password_env, role.bearer_env) if name and environ.get(name)
        }

    def login_payload(self, role_name: str) -> dict[str, str]:
        role = self.config.roles[role_name]
        return {role.username_field: self._environ[role.username_env],
                role.password_field: self._environ[role.password_env]}

    def capture(self, role_name: str, body: Any) -> None:
        role = self.config.roles[role_name]
        token = select(body, role.token_selector)
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise ValueError("Invalid authentication response")
        self._tokens[role_name] = token
        self._secrets.add(token)

    def header(self, role_name: str) -> dict[str, str]:
        role = self.config.roles[role_name]
        token = self._tokens.get(role_name)
        if not token and role.bearer_env:
            token = self._environ.get(role.bearer_env)
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("Missing authentication context")
        return {"Authorization": "Bearer " + token}

    def contains_secret(self, value: Any) -> bool:
        if isinstance(value, dict):
            return any(self.contains_secret(k) or self.contains_secret(v) for k, v in value.items())
        if isinstance(value, list):
            return any(self.contains_secret(v) for v in value)
        return value is not None and any(secret in str(value) for secret in self._secrets)

    def public_response(self, value: Any) -> Any:
        """Remove whole sensitive subtrees and secret aliases, including numeric values."""
        if isinstance(value, dict):
            return {k: self.public_response(v) for k, v in value.items()
                    if not sensitive_field(k, self.config.sensitive_fields)
                    and not self.contains_secret(k) and not self.contains_secret(v)}
        if isinstance(value, list):
            return [self.public_response(v) for v in value if not self.contains_secret(v)]
        return None if self.contains_secret(value) else value
