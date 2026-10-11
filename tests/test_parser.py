from pathlib import Path

import pytest

from agenttest.contracts import MAX_DOCUMENT_BYTES, load_openapi

FIXTURE = Path(__file__).parent / "fixtures" / "contract.yaml"


def test_yaml_and_json_local_references() -> None:
    summary = load_openapi(FIXTURE.read_bytes())
    operation = next(o for o in summary.operations if o.method == "POST")
    assert operation.parameters[0]["name"] == "id"
    assert operation.request_body["content"]["application/json"]["schema"]["type"] == "object"
    assert operation.security == [{"bearerAuth": []}]
    assert set(operation.responses) == {"200"}
    assert load_openapi(Path("examples/minimal-openapi.json").read_bytes()).version == "3.0.3"


@pytest.mark.parametrize("raw", [
    b"a: [", b"[]", b"!!python/object:evil {}", b"a: 1\na: 2",
    b"a: &a [*a]", b"a: !!binary abc", b"a: .nan",
])
def test_reject_malformed_or_unsafe_yaml(raw: bytes) -> None:
    with pytest.raises(ValueError):
        load_openapi(raw)


@pytest.mark.parametrize("ref", ["https://evil.test/a", "file:///secret", "#/missing"])
def test_references_never_fetch(ref: str) -> None:
    with pytest.raises(ValueError):
        load_openapi(FIXTURE.read_text() + f"\nextra: {{$ref: '{ref}'}}")


def test_cyclic_reference_and_size_limits() -> None:
    with pytest.raises(ValueError):
        load_openapi(b" " * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(ValueError):
        load_openapi(FIXTURE.read_text() + "\nextra: {$ref: '#/extra'}")
    with pytest.raises(ValueError):
        load_openapi("a: " + "[" * 60 + "0" + "]" * 60)


def test_reference_expansion_byte_limit() -> None:
    schema = "description: '" + "x" * 50_000 + "'"
    repeated = "\n".join(f"    x{i}: {{$ref: '#/components/schemas/base'}}" for i in range(25))
    with pytest.raises(ValueError, match="Expanded reference size"):
        load_openapi("openapi: 3.1.0\ninfo: {title: x, version: '1'}\npaths: {}\n"
                     "components:\n  schemas:\n    base: {" + schema + "}\n" + repeated)


@pytest.mark.parametrize("keyword", ["$dynamicRef", "$recursiveRef"])
def test_dynamic_references_never_reach_validator(keyword: str) -> None:
    with pytest.raises(ValueError, match="Dynamic references"):
        load_openapi(FIXTURE.read_text() + f"\nextra: {{{keyword}: 'https://evil.test/schema'}}")


def test_reference_siblings_cannot_change_schema_meaning() -> None:
    with pytest.raises(ValueError, match="constraint siblings"):
        load_openapi(FIXTURE.read_text()
                     + "\nextra: {$ref: '#/components/schemas/Item', type: string}")
