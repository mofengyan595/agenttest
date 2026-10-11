"""Bounded public planning input; no server URLs, examples, accounts or response data."""

import re
import unicodedata

from agenttest.contracts import ContractSummary
from agenttest.policies import PolicyRegistry
from agenttest.request_validation import CONSTRAINTS
from agenttest.safety import TargetConfig

PRIVATE_LITERAL = re.compile(
    r"https?://|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|\bsk-[A-Za-z0-9_-]{12,}|(?:password|api[_ -]?key|token|密码)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def validate_user_task(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 2000
            or len(value.encode("utf-8")) > 8000
            or any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in value)
            or PRIVATE_LITERAL.search(value)):
        raise ValueError("Unsafe user task")
    return value.strip()


def public_schema(schema: dict) -> dict:
    """Remove free-form annotations/defaults, retaining validation constraints only."""
    result = {}
    for key, value in schema.items():
        if key not in CONSTRAINTS:
            continue
        if key == "properties":
            result[key] = {name: public_schema(child) for name, child in value.items()}
        elif key == "items":
            result[key] = public_schema(value)
        else:
            result[key] = value
    return result


def planning_context(contract: ContractSummary, config: TargetConfig,
                     registry: PolicyRegistry, user_task: str, plan_schema: dict) -> dict:
    policies = registry.catalog(config.business_scenario)
    permitted = {p["operation_id"] for p in policies} & set(config.allowed_operations)
    operations = []
    for op in contract.operations:
        if op.operation_id not in permitted:
            continue
        try:
            config.permit(op.operation_id, op.method)
        except ValueError:
            continue
        managed = any(role.login_operation == op.operation_id for role in config.roles.values())
        body = None
        if op.request_body and not managed:
            body = {"required": op.request_body.get("required", False), "content": {
                media: public_schema(value.get("schema", {}))
                for media, value in op.request_body.get("content", {}).items()}}
        operations.append({
            "id": op.operation_id, "method": op.method, "path": op.path,
            "parameters": [{"name": p["name"], "in": p["in"],
                            "required": p.get("required", False),
                            "schema": public_schema(p.get("schema", {}))}
                           for p in op.parameters],
            "request_body": body, "authentication_executor_managed": managed,
            "security": op.security,
        })
    return {"user_task": validate_user_task(user_task), "operations": operations,
            "policies": [p for p in policies if p["operation_id"] in permitted],
            "roles": list(config.roles), "plan_schema": plan_schema}
