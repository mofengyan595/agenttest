import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_product_design import case, project, settings

from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design import design_context, design_groups


def test_schema_pruning_keeps_reachable_cycles_and_has_identical_validation():
    from agenttest.product_schema import prune_schema_definitions

    schema = dict(type="object", properties=dict(node={"$ref": "#/$defs/Node"}),
        additionalProperties=False, **{"$defs": dict(Node={"anyOf": [dict(type="null"),
            dict(type="array", items={"$ref": "#/$defs/Node"})]},
            Unused=dict(type="string"))})
    original = Draft202012Validator(copy.deepcopy(schema))
    prune_schema_definitions(schema)
    assert set(schema["$defs"]) == {"Node"}
    current = Draft202012Validator(schema)
    for value in ({}, {"node": None}, {"node": [None, [None]]}, {"node": "bad"}, {"other": 1}):
        assert current.is_valid(value) == original.is_valid(value)


def test_permission_question_wire_requires_available_identity_and_read_operation():
    proj, _ = project()
    context = design_context(proj, "先做独立检查，权限规则待确认", design_groups(proj)[0], 10, 2)

    def validator(roles):
        context["available_identity_aliases"] = roles
        body = json.loads(DesignModel(settings(), profile="generic").prepare_request(context))
        return Draft202012Validator(body["response_format"]["json_schema"]["schema"])

    question = dict(operation_id="readJob", question="reader应当403还是404？",
        requested_behavior="读取他人任务", kind="permission", role="reader",
        status_choices=[403, 404])
    proposal = dict(interpretation="等待权限规则确认", relevant=True, cases=[],
                    questions=[question], not_covered=[])
    assert not validator([]).is_valid(proposal)
    approved = validator(["reader"])
    assert approved.is_valid(proposal)
    for change in (dict(role=None), dict(role="invented"), dict(operation_id="insertJob"),
                   dict(status_choices=[]), dict(status_choices=[200])):
        assert not approved.is_valid({**proposal, "questions": [{**question, **change}]})


def schema_for(proj):
    context = design_context(proj, "设计正常和受控负例", design_groups(proj)[0], 40, 2)
    payload = json.loads(DesignModel(settings(), profile="generic").prepare_request(context))
    schema = payload["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    return schema


def proposal(design_case):
    return dict(interpretation="创建受支持的任务", relevant=True,
                cases=[design_case], questions=[], not_covered=[])


def test_wire_schema_covers_all_policy_ids_and_requires_valid_negative_baselines():
    proj, _ = project(negative=True)
    schema = schema_for(proj)
    validate = Draft202012Validator(schema)
    policies = proj.registry.definitions()
    for policy in policies:
        if policy.operation_id == "insertJob":
            ordinary = case(policy=policy.id)
            candidate = (dict(title=ordinary["title"], before=[], after=[],
                              check=ordinary["plan"]["steps"][0])
                         if policy.negative else ordinary)
            validate.validate(proposal(candidate))
    negative = next(p for p in policies if p.negative and p.negative.name == "quantity")
    for invalid in [0, 4, True, "2"]:
        invalid_case = case(policy=negative.id, quantity=invalid)
        assert not validate.is_valid(proposal(dict(title=invalid_case["title"], before=[], after=[],
            check=invalid_case["plan"]["steps"][0])))
    missing = case(policy=negative.id)
    missing["plan"]["steps"][0]["arguments"].pop()
    assert not validate.is_valid(proposal(dict(title=missing["title"], before=[], after=[],
        check=missing["plan"]["steps"][0])))
    # The source mutation remains executor-owned; schema generation changes no approval.
    assert all(p.negative is None or p.assertions.status.codes == [422] for p in policies)


def test_clarification_questions_must_refer_to_an_operation_in_the_group():
    proj, _ = project()
    validator = Draft202012Validator(schema_for(proj))
    question = dict(operation_id="insertJob", kind="clarification", role=None,
        status_choices=[], question="创建后的通知应在多少秒内完成？", requested_behavior="通知期限")
    draft = {**proposal(case()), "questions": [question]}
    assert validator.is_valid(draft)
    for operation in (None, "outside_group"):
        assert not validator.is_valid({**draft, "questions": [{**question,
                                                               "operation_id": operation}]})


@pytest.mark.parametrize("change", [dict(operation_id="readJob"), dict(policy_id="invented"),
    dict(role="unapproved")])
def test_wire_schema_rejects_mismatched_operation_policy_or_role(change):
    proj, _ = project()
    design = case()
    design["plan"]["steps"][0].update(change)
    assert not Draft202012Validator(schema_for(proj)).is_valid(proposal(design))


def test_wire_schema_preserves_nested_enums_and_container_shape():
    proj, _ = project()
    validate = Draft202012Validator(schema_for(proj))
    design = case()
    assert validate.is_valid(proposal(design))
    invalid = copy.deepcopy(design)
    nested = invalid["plan"]["steps"][0]["arguments"][2]
    nested["value"]["object"][0]["value"]["array"] = ["not-approved"]
    assert not validate.is_valid(proposal(invalid))


def test_large_default_round_keeps_all_ready_operations_under_each_model_input_budget():
    from test_colleague_targets import targets
    from test_project_inventory import proposed_setup

    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import build_generic_project

    raw = json.dumps(targets.document("directory"))
    contract = load_openapi(raw)
    setup = proposed_setup(contract)
    setup.approve_negative_tests = setup.allow_negative_value_variants = True
    setup.max_requests = 64
    proj = build_generic_project("directory", raw, contract, setup, environ={})
    groups = design_groups(proj)
    assert len(groups) == 8 and sum(len(g["primary"]) for g in groups) == 45
    observed = []
    for group in groups:
        context = design_context(proj, "先安排一轮常规检查，说明还有哪些没有覆盖", group, 64, 48)
        context["required_normal_operations"] = group["primary"]
        context["suggested_group_http_requests"] = len(group["primary"]) + 2
        model = DesignModel(settings(), profile="generic")
        body = json.loads(model.prepare_request(context))
        Draft202012Validator.check_schema(body["response_format"]["json_schema"]["schema"])
        assert model.stats.input_token_upper_bound <= 60_000
        public = json.loads(body["messages"][1]["content"])
        assert len(public["confirmed_rules"]) == len(context["confirmed_rules"])
        assert public["required_normal_operations"] == group["primary"]
        observed.extend(public["primary_operations"])
    assert set(observed) == set(proj.target.allowed_operations)


def test_step_constraint_sharing_preserves_policy_role_and_input_validation():
    from agenttest.product_schema import share_step_arguments

    step = dict(type="object", additionalProperties=False,
        properties=dict(id=dict(type="string"), operation_id=dict(enum=["read"]),
            policy_id=dict(enum=["approved"]), role=dict(enum=["reader"]),
            arguments=dict(type="array", minItems=1, maxItems=1,
                           items=dict(type="integer", minimum=1, maximum=3))),
        required=["id", "operation_id", "policy_id", "role", "arguments"])
    schema = dict(type="object", additionalProperties=False,
        properties=dict(first=copy.deepcopy(step), second=copy.deepcopy(step)),
        required=["first", "second"], **{"$defs": {}})
    original = Draft202012Validator(copy.deepcopy(schema))
    share_step_arguments(schema)
    Draft202012Validator.check_schema(schema)
    shared = Draft202012Validator(schema)
    assert schema["properties"]["first"]["$ref"] == schema["properties"]["second"]["$ref"]
    valid = dict(id="one", operation_id="read", policy_id="approved", role="reader", arguments=[2])
    assert shared.is_valid(dict(first=valid, second=valid))
    for change in [dict(policy_id="unapproved"), dict(operation_id="other"), dict(role="admin"),
                   dict(arguments=[0]), dict(arguments=[4]), dict(arguments=[True]), dict(extra=1)]:
        value = dict(first=valid, second={**valid, **change})
        assert not shared.is_valid(value)
        assert shared.is_valid(value) == original.is_valid(value)
