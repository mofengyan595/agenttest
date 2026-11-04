import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_product_design import case, project, settings

from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design import design_context, design_groups


def test_shared_schema_preserves_nested_constraints_and_closed_objects():
    from agenttest.product_schema import share_repeated_schemas

    child = dict(type='object', additionalProperties=False, properties=dict(
        field=dict(type='string', enum=['page', 'size']),
        value=dict(type='integer', minimum=0, maximum=4)), required=['field', 'value'])
    schema = dict(type='object', additionalProperties=False,
        properties={key: copy.deepcopy(child) for key in ('teacher', 'student')},
        required=['teacher', 'student'])
    original = copy.deepcopy(schema)
    share_repeated_schemas(schema)
    Draft202012Validator.check_schema(schema)
    assert '$ref' in schema['properties']['teacher']
    assert len(json.dumps(schema)) < len(json.dumps(original))
    valid = dict(teacher=dict(field='page', value=1), student=dict(field='size', value=4))
    variants = [valid, {**valid, 'extra': 1}, {**valid, 'teacher': dict(field='size', value=-1)},
        {**valid, 'student': dict(field='other', value=2)},
        {**valid, 'student': dict(field='size', value=2, extra=1)}]
    for value in variants:
        assert Draft202012Validator(schema).is_valid(value) == (
            Draft202012Validator(original).is_valid(value))


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


def test_fixed_resource_wire_rejects_duplicate_ordinary_setups_only():
    from agenttest.llm_planner import literal_data
    from agenttest.product_design import DesignCase
    from agenttest.product_schema import decode_fixed_resource_setups

    proj, _ = project()
    context = design_context(proj, "创建本轮资源并由其他身份读取", design_groups(proj)[0], 40, 2)
    context["fresh_resource_operations"] = ["insertJob"]
    ordinary = case()
    values = {a.name: literal_data(a.value)
              for a in DesignCase.model_validate(ordinary).plan.steps[0].arguments}
    for policy in context["policies"]:
        if policy["operation_id"] == "insertJob":
            for rule in policy["binding_rules"]:
                rule["literal_schema"]["enum"] = [values[rule["name"]]]
    chained = copy.deepcopy(context["policies"][1])
    chained["id"] = "follow_read"
    chained["binding_rules"][0]["source_policy_id"] = "web_rule_2"
    access = {**copy.deepcopy(chained), "id": "follow_access",
              "binding_rules_from_policy": "follow_read"}
    access.pop("binding_rules")
    context["policies"].extend([chained, access])
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    assert set(context["fixed_resource_dependent_policies"]) == {
        "web_rule_2", "follow_read", "follow_access"}
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    wire = {**proposal(ordinary), "cases": [], "resource_flows": {"fresh_1": ordinary}}
    assert validator.is_valid(wire)
    for identifier in ("web_rule_2", "follow_read", "follow_access"):
        outside = dict(title="依赖本轮新资源的查询", kind="normal", plan=dict(name="outside",
            steps=[dict(id="read", operation_id="readJob", policy_id=identifier, role=None,
                arguments=[dict(location="path_parameters", name="jobId", value=None,
                                from_step="insert", select="$.id")])]))
        assert not validator.is_valid({**wire, "cases": [outside]})
    assert not validator.is_valid({**wire, "cases": [ordinary]})
    assert not validator.is_valid({**wire, "resource_flows": {"fresh_1": ordinary, "extra": None}})
    assert validator.is_valid({**wire, "resource_flows": {"fresh_1": None}})
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    assert decoded["cases"] == [ordinary]
    duplicate = {**copy.deepcopy(wire), "cases": [ordinary]}
    with pytest.raises(ValueError, match="DESIGN_DUPLICATE_RESOURCE_SETUP"):
        decode_fixed_resource_setups(duplicate, context)
    context["policies"][0]["binding_rules"][0]["literal_schema"].pop("enum")
    context.pop("fixed_resource_setups", None)
    variable_schema = json.loads(model.prepare_request(context))[
        "response_format"]["json_schema"]["schema"]
    assert "resource_flows" not in variable_schema["properties"]
    assert "fixed_resource_dependent_policies" not in context


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


def test_fresh_resource_consumers_must_share_their_producer_case():
    from agenttest.llm_planner import literal_data
    from agenttest.product_design import DesignCase
    from agenttest.product_schema import decode_fixed_resource_setups

    proj, _ = project()
    context = design_context(proj, "创建资源并查询同一资源", design_groups(proj)[0], 40, 2)
    context["fresh_resource_operations"] = ["insertJob"]
    fresh = case()
    values = {a.name: literal_data(a.value)
              for a in DesignCase.model_validate(fresh).plan.steps[0].arguments}
    for policy in context["policies"]:
        if policy["operation_id"] == "insertJob":
            for rule in policy["binding_rules"]:
                rule["literal_schema"]["enum"] = [values[rule["name"]]]
    read = dict(id="read", operation_id="readJob", policy_id="web_rule_2", role=None,
        arguments=[dict(location="path_parameters", name="jobId", value=None,
                        from_step="insert", select="$.id")])
    separate = dict(title="读取本轮新资源", kind="normal",
                    plan=dict(name="read-new", steps=[read]))
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    wire = {**proposal(fresh), "cases": [separate], "resource_flows": {"fresh_1": fresh}}
    assert not Draft202012Validator(schema).is_valid(wire)
    with pytest.raises(ValueError, match="DESIGN_RESOURCE_DEPENDENCY_OUTSIDE_FLOW"):
        decode_fixed_resource_setups(copy.deepcopy(wire), context)
    wire["cases"] = []
    fresh["plan"]["steps"].append(read)
    Draft202012Validator(schema).validate(wire)
    assert len(json.loads(model._decode_output(json.dumps(wire), context))["cases"]) == 1


@pytest.mark.parametrize("mandatory", [False, True])
def test_unique_resource_slots_preserve_independent_approved_negative_cases(mandatory):
    from agenttest.llm_planner import literal_data
    from agenttest.product_design import DesignCase

    proj, _ = project(negative=True)
    context = design_context(proj, "创建一次并检查已批准的无效数量", design_groups(proj)[0], 40, 2)
    context["fresh_resource_operations"] = ["insertJob"]
    values = {a.name: literal_data(a.value)
              for a in DesignCase.model_validate(case()).plan.steps[0].arguments}
    for policy in context["policies"]:
        if policy["operation_id"] == "insertJob" and not policy.get("negative_test"):
            for rule in policy["binding_rules"]:
                rule["literal_schema"]["enum"] = [values[rule["name"]]]
    negative = next(p for p in proj.registry.definitions() if p.negative
                    and p.negative.name == "quantity" and p.negative.kind == "boundary_outside")
    if mandatory:
        context["requested_checks"] = [dict(policy_id=negative.id,
                                             source_quote=context["user_task"])]
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    rejected = dict(title="检查无效数量", before=[], after=[],
                    check=case(policy=negative.id)["plan"]["steps"][0])
    wire = {**proposal(case()), "cases": [], "resource_flows": {"fresh_1": case()}}
    if mandatory:
        wire["required_cases"] = {negative.id: rejected}
    else:
        wire["cases"] = [rejected]
    Draft202012Validator(schema).validate(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    assert len(decoded["cases"]) == 2
    assert {c["kind"] for c in decoded["cases"]} == {"normal", "negative"}
    assert sum(s["policy_id"] == negative.id for c in decoded["cases"]
               for s in c["plan"]["steps"]) == 1


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
