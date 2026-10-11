"""Bounded OpenAPI input and local-only references; never fetch documents."""

import json
import re
from typing import Any

import yaml
from pydantic import BaseModel, Field

MAX_DOCUMENT_BYTES = 1_000_000
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class Operation(BaseModel):
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_body: dict[str, Any] | None = None
    responses: dict[str, Any] = Field(default_factory=dict)
    security: list[dict[str, list[str]]] = Field(default_factory=list)


class ContractSummary(BaseModel):
    version: str
    operations: list[Operation] = Field(default_factory=list)
    security_schemes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class UniqueLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys and YAML merge keys."""


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("Document keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_openapi(raw: bytes | str, *, unwrap: bool = False) -> ContractSummary:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValueError("Contract exceeds size limit")
    try:
        document = yaml.load(encoded.decode("utf-8"), Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid JSON/YAML document") from exc
    if unwrap and isinstance(document, dict) and set(document) == {"document"}:
        document = document["document"]
    return summarize_openapi(document)


def _bounded_tree(document: Any) -> None:
    count = 0

    def walk(value: Any, depth: int, ancestors: set[int]) -> None:
        nonlocal count
        count += 1
        if depth > 40 or count > 20_000:
            raise ValueError("Contract nesting or expansion exceeds limit")
        if isinstance(value, (dict, list)):
            if id(value) in ancestors:
                raise ValueError("Cyclic YAML aliases are forbidden")
            ancestors = ancestors | {id(value)}
            if isinstance(value, dict) and any(not isinstance(k, str) for k in value):
                raise ValueError("Document keys must be strings")
            for child in value.values() if isinstance(value, dict) else value:
                walk(child, depth + 1, ancestors)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("Document must contain JSON-compatible values")

    walk(document, 0, set())
    if len(json.dumps(document, allow_nan=False).encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError("Contract exceeds expanded size limit")


def _resolve(document: dict) -> dict:
    budget = 0

    def visit(value: Any, trail: tuple[str, ...] = (), depth: int = 0) -> Any:
        nonlocal budget
        budget += 1
        if budget > 30_000 or depth > 50:
            raise ValueError("Reference expansion exceeds limit")
        if isinstance(value, list):
            return [visit(item, trail, depth + 1) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            ref = value["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in trail:
                raise ValueError("Only acyclic local JSON Pointer references are supported")
            target: Any = document
            try:
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                raise ValueError("Unresolved local reference") from exc
            if not isinstance(target, dict):
                raise ValueError("Reference must resolve to an object")
            resolved = visit(target, (*trail, ref), depth + 1)
            siblings = {k: visit(v, trail, depth + 1) for k, v in value.items() if k != "$ref"}
            return {**resolved, **siblings}
        return {key: visit(item, trail, depth + 1) for key, item in value.items()}

    return visit(document)


def summarize_openapi(document: dict[str, Any]) -> ContractSummary:
    _bounded_tree(document)
    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must be an object")
    version = document.get("openapi")
    if not isinstance(version, str) or not re.fullmatch(r"3\.[01]\.\d+", version):
        raise ValueError("Only OpenAPI 3.0/3.1 specifications are supported")
    info = document.get("info")
    if not isinstance(info, dict) or not all(
        isinstance(info.get(k), str) for k in ("title", "version")
    ):
        raise ValueError("OpenAPI info.title and info.version are required")
    document = _resolve(document)
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI document must provide a paths object")
    components = document.get("components", {})
    if not isinstance(components, dict):
        raise ValueError("components must be an object")
    schemes = components.get("securitySchemes", {})
    if not isinstance(schemes, dict) or any(not isinstance(v, dict) for v in schemes.values()):
        raise ValueError("securitySchemes must contain objects")
    operations = []
    ids: set[str] = set()
    for path, path_spec in paths.items():
        if path.startswith("x-"):
            continue
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "?#\\"):
            raise ValueError("Each path must be a local absolute API path")
        if not isinstance(path_spec, dict):
            raise ValueError("Path item must be an object")
        for method, operation in path_spec.items():
            if method not in METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError("Operation must be an object")
            operation_id = operation.get("operationId")
            if operation_id is not None:
                if not isinstance(operation_id, str) or not operation_id or operation_id in ids:
                    raise ValueError("operationId must be a unique nonempty string")
                ids.add(operation_id)
            parameters: dict[tuple[str, str], dict] = {}
            for owner in (path_spec, operation):
                values = owner.get("parameters", [])
                if not isinstance(values, list):
                    raise ValueError("parameters must be an array")
                for param in values:
                    if (not isinstance(param, dict) or not isinstance(param.get("name"), str)
                            or param.get("in") not in {"path", "query", "header", "cookie"}):
                        raise ValueError("Invalid parameter")
                    if param["in"] == "path" and param.get("required") is not True:
                        raise ValueError("Path parameters must be required")
                    parameters[param["in"], param["name"]] = param
            body = operation.get("requestBody")
            if body is not None and (not isinstance(body, dict)
                                     or not isinstance(body.get("content"), dict)):
                raise ValueError("requestBody must contain content")
            responses = operation.get("responses", {})
            if not isinstance(responses, dict) or any(
                not re.fullmatch(r"[1-5][0-9X]{2}|default", status)
                or not isinstance(response, dict) for status, response in responses.items()
            ):
                raise ValueError("Invalid response definitions")
            security = operation.get("security", document.get("security", []))
            if not isinstance(security, list) or any(
                not isinstance(requirement, dict) or any(
                    name not in schemes or not isinstance(scopes, list)
                    or any(not isinstance(scope, str) for scope in scopes)
                    for name, scopes in requirement.items()
                ) for requirement in security
            ):
                raise ValueError("Invalid or unknown security requirements")
            operations.append(Operation(
                method=method.upper(), path=path, operation_id=operation_id,
                summary=operation.get("summary"), parameters=list(parameters.values()),
                request_body=body, responses=responses, security=security,
            ))
    operations.sort(key=lambda item: (item.path, item.method))
    return ContractSummary(version=version, operations=operations, security_schemes=schemes)
