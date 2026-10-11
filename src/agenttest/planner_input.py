"""Bounded public planning input; no server URLs, examples, accounts or response data."""

import json
import re
import unicodedata

from agenttest.contracts import ContractSummary
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.request_validation import CONSTRAINTS, request_schemas
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
        support = "SUPPORTED_SUBSET"
        try:
            media_types = list((op.request_body or {}).get("content", {})) or [None]
            for media in media_types:
                request_schemas(op, media)
        except (NeedsReview, ValueError):
            support = "NEEDS_REVIEW"
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
            "request_validation": support,
        })
    visible_ids = {op["id"] for op in operations}
    visible_policies = [p for p in policies if p["operation_id"] in visible_ids]
    for policy in visible_policies:
        for rule in policy["binding_rules"]:
            if rule["literal_schema"] is not None:
                rule["literal_schema"] = public_schema(rule["literal_schema"])
    schemes = {name for op in operations for requirement in op["security"] for name in requirement}
    context = {"user_task": validate_user_task(user_task), "operations": operations,
               "security_schemes": {name: {key: value for key, value in definition.items()
                                          if key in {"type", "scheme"}}
                                    for name, definition in contract.security_schemes.items()
                                    if name in schemes},
               "policies": visible_policies, "roles": list(config.roles),
               "plan_schema": plan_schema}
    raw = json.dumps(context, ensure_ascii=False)
    if len(raw.encode()) > 100_000:
        raise ValueError("Planner context too large")
    pending = [context]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and PRIVATE_LITERAL.search(value):
            raise ValueError("Sensitive literal in public planning input")
    return context
