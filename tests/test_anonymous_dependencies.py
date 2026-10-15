"""Anonymous resource flow requires a distinct operator-owned relationship."""

import pytest

from agenttest.dsl import ArgumentBinding, Step
from agenttest.policies import NeedsReview, PolicyRegistry, RegisteredPolicy


def inputs(relation, source_role=None, reader_role=None):
    source = Step(id="listing", operation_id="list", role=source_role, policy_id="list_v1")
    reader = Step(id="detail", operation_id="get", role=reader_role, policy_id="get_v1",
                  path_parameters={"id": ArgumentBinding(from_step="listing", select="$[0].id")})
    policy = RegisteredPolicy(id="get_v1", operation_id="get", roles=[reader_role],
        business_scenario="offline", closed_arguments=True,
        assertions={"reviewed": True, "source": "synthetic reviewed fixture",
                    "status": {"codes": [200]}},
        binding_rules=[{"location": "path_parameters", "name": "id",
                        "source_policy_id": "list_v1", "source_selector": "$[0].id",
                        "role_relation": relation}])
    return PolicyRegistry([policy]), source, reader


@pytest.mark.parametrize("relation,source_role,reader_role,approved", [
    ("anonymous", None, None, True),
    ("anonymous", "alice", None, False), ("anonymous", None, "alice", False),
    ("anonymous", "alice", "alice", False),
    (None, None, None, False), (None, "alice", None, False),
    ("same", None, None, False), ("different", None, None, False),
    ("same", "alice", "alice", True), ("same", "alice", "bob", False),
    ("different", "alice", "bob", True), ("different", "alice", "alice", False),
])
def test_anonymous_grant_does_not_change_existing_identity_rules(
        relation, source_role, reader_role, approved):
    registry, source, reader = inputs(relation, source_role, reader_role)
    if approved:
        assert registry.resolve(reader, "offline", {source.id: source}).id == "get_v1"
    else:
        with pytest.raises(NeedsReview, match="POLICY_DEPENDENCY_MISMATCH"):
            registry.resolve(reader, "offline", {source.id: source})


@pytest.mark.parametrize("mutation", ["missing_source", "wrong_policy", "wrong_selector"])
def test_anonymous_relation_still_requires_exact_approved_producer_and_selector(mutation):
    registry, source, reader = inputs("anonymous")
    previous = {source.id: source}
    if mutation == "missing_source":
        previous = {}
    elif mutation == "wrong_policy":
        source.policy_id = "unapproved"
    else:
        reader.path_parameters["id"].select = "$[1].id"
    with pytest.raises(NeedsReview, match="POLICY_DEPENDENCY_MISMATCH"):
        registry.resolve(reader, "offline", previous)
