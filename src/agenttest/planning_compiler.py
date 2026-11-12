"""Deterministic planning compiler: IR instances plus a Design proposal become one DSL plan.

The compiler owns the executable facts a model used to manage by itself:

* which proposal step realizes which ``ActionInstance`` (never the array position),
* which approved policy and response assertion each instance carries,
* which argument value comes from which producing instance,
* the execution order (stable topological order of the IR dependency graph).

A proposal that does not account for exactly one step per instance, names an unsourced clause,
uses a self-made data reference in the wrong direction, or disagrees with the IR policy/status
is rejected with a precise diagnostic. No edge is deleted and no order is guessed to make a
proposal pass.
"""

from __future__ import annotations

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import GenericPlannerDraft
from agenttest.planning_ir import HARD_EDGE_REASONS, ActionInstance, PlanningIR

ENCODING = "NORMALIZED_IR_COMPILER_V1"
# The IR marks a read instance it derived from an approved 404/410 rule with this origin.
ABSENCE_ORIGIN = "APPROVED_ABSENCE_POLICY"


def _failure(code, fields=None, construction=None):
    error = ValidationFailure(Layer.MODEL, code)
    if fields:
        error.diagnostic.fields = list(fields)[:8]
    if construction:
        error.construction = construction
        error.diagnostic.detail = _repair_facts(construction)
    return error


def _repair_facts(detail):
    """Keep the repair-relevant facts small and free of request/response values."""
    kept = {}
    for key, value in list(detail.items())[:10]:
        if isinstance(value, str):
            kept[key] = value[:160]
        elif isinstance(value, (int, float, bool)) or value is None:
            kept[key] = value
        elif isinstance(value, (list, tuple)):
            kept[key] = [str(item)[:80] for item in list(value)[:6]]
        elif isinstance(value, dict):
            kept[key] = {str(k)[:40]: str(v)[:80] for k, v in list(value.items())[:6]}
    return kept


def _instance_groups(ir: PlanningIR):
    """Instances grouped by the clause that produced them, in deterministic order."""
    groups: dict[str, list[ActionInstance]] = {}
    for instance in ir.instances:
        groups.setdefault(instance.source_id, []).append(instance)
    for members in groups.values():
        members.sort(key=lambda item: item.action_index)
    return groups


def _instance_statuses(instance):
    """Explicit status assertions the IR attached to one request instance."""
    return {status for assertion in instance.assertions for status in assertion.statuses}


def _policy_statuses(policy):
    if not isinstance(policy, dict):
        return set()
    status = ((policy.get("assertions") or {}).get("status") or {})
    return {code for code in status.get("codes") or [] if isinstance(code, int)}


def _argument_slots(step):
    return {(argument.location, argument.name): argument for argument in step.arguments}


def _resource_references(step, mapping):
    """Resolved (producer instance, selector) pairs a step already exposes."""
    return {(mapping[argument.from_step], argument.select)
            for argument in step.arguments
            if argument.from_step is not None and argument.from_step in mapping}


def _rank_instance(step, instance, mapping, policies):
    """Score only approved semantics; no score means the model did not identify the instance."""
    score, criteria = 0, []
    if step.policy_id == instance.policy_id:
        score += 8
        criteria.append("POLICY")
    step_statuses = _policy_statuses(policies.get(step.policy_id))
    if step_statuses & _instance_statuses(instance):
        score += 4
        criteria.append("STATUS")
    references = _resource_references(step, mapping)
    instance_references = {(argument.from_instance, argument.select)
                           for argument in instance.arguments
                           if argument.from_instance is not None}
    if references:
        if references <= instance_references:
            score += 4
            criteria.append("RESOURCE")
        elif instance_references:
            score -= 4
    step_arguments = _argument_slots(step)
    instance_arguments = _argument_slots(instance)
    for slot, argument in step_arguments.items():
        target = instance_arguments.get(slot)
        if (target is not None and target.from_instance is None
                and argument.from_step is None):
            if argument.value == target.value:
                score += 1
                criteria.append("ARGUMENT")
            else:
                score -= 2
    for slot, target in instance_arguments.items():
        if target.from_instance is not None:
            supplied = step_arguments.get(slot)
            if supplied is None or supplied.from_step is None:
                score -= 2
    return score, criteria


def _step_instance_map(ir: PlanningIR, draft, policies=None):
    """Map proposal steps to IR instances using approved semantics, never array position.

    A clause with explicit ``requirement_source_id`` bindings owns its instances and consumes
    them in that order. Different requests may never share one identity. When a repeated
    operation has no source binding, the program first uses the approved policy, the response
    status assertion, resolved producer references and literal argument slots. A unique winner
    is bound; a tie remains ``NEEDS_REVIEW`` instead of being guessed. Byte-identical repeated
    steps with no distinguishing evidence are therefore deliberately not distributed by array
    position.
    """
    policies = policies or {}
    groups = _instance_groups(ir)
    by_operation: dict[tuple[str, str | None], list[ActionInstance]] = {}
    for instance in ir.instances:
        by_operation.setdefault((instance.operation_id, instance.role), []).append(instance)
    mapping: dict[str, str] = {}
    by_instance: dict[str, list[str]] = {}
    used_instances: set[str] = set()
    used_clauses: dict[str, int] = {}
    unresolved = []
    for position, step in enumerate(draft.steps):
        source = step.requirement_source_id
        index = step.requirement_action_index or 0
        if source is None:
            unresolved.append((position, step))
            continue
        all_members = list(groups.get(source, []))
        # The operation id is part of the identity: a clause may own a delete and an approved
        # post-delete readback at the same action index, and a proposal step must never be
        # mapped to an instance of a different operation just because the index matches.
        members = [item for item in all_members if item.action_index == index
                   and item.operation_id == step.operation_id]
        if not members and index == 0:
            members = [item for item in all_members if item.operation_id == step.operation_id]
        if not members:
            raise _failure("PLANNING_UNSOURCED_STEP",
                           [f"plan.steps.{position}.requirement_source_id"],
                           dict(clause=source, index=index,
                                message="该子句不是本轮可执行的请求实例。"))
        cursor = used_clauses.get(source, 0)
        instance = members[cursor] if cursor < len(members) else None
        if instance is None:
            # A model may reuse index 0 for a second call from the same clause when an
            # approved expectation adds a distinct instance. The operation identity is
            # stronger evidence than the repeated index; never guess across operations.
            instance = next((item for item in all_members
                             if item.instance_id not in used_instances
                             and item.operation_id == step.operation_id), None)
        if instance is None:
            raise _failure("PLANNING_INSTANCE_CONFLICT",
                           [f"plan.steps.{position}.requirement_source_id"],
                           dict(clause=source, instances=[item.instance_id
                                                         for item in members],
                                message="模型为一个请求实例计划了多个不同步骤。"))
        used_clauses[source] = cursor + 1
        mapping[step.id] = instance.instance_id
        used_instances.add(instance.instance_id)
        by_instance.setdefault(instance.instance_id, []).append(step.id)

    while unresolved:
        progress = False
        deferred = []
        for position, step in unresolved:
            candidates = [item for item in by_operation.get(
                (step.operation_id, step.role), []) if item.instance_id not in used_instances]
            if not candidates:
                raise _failure(
                    "PLANNING_INSTANCE_UNBOUND", [f"plan.steps.{position}"],
                    dict(operation_id=step.operation_id, role=step.role, candidates=[],
                         message="没有剩余的同操作实例可绑定；程序不会按数组位置猜测。"))
            if len(candidates) == 1:
                chosen = candidates[0]
            else:
                ranked = [(_rank_instance(step, item, mapping, policies), item)
                          for item in candidates]
                best = max(score for (score, _), _ in ranked)
                top = [item for (score, criteria), item in ranked if score == best]
                if best <= 0 or len(top) != 1:
                    deferred.append((position, step, candidates, ranked))
                    continue
                chosen = top[0]
            mapping[step.id] = chosen.instance_id
            used_instances.add(chosen.instance_id)
            by_instance.setdefault(chosen.instance_id, []).append(step.id)
            progress = True
        if not progress:
            position, step, candidates, ranked = deferred[0]
            evidence = [
                dict(instance_id=item.instance_id, score=score, criteria=criteria,
                     policy_id=item.policy_id, statuses=sorted(_instance_statuses(item)))
                for (score, criteria), item in ranked]
            raise _failure(
                "PLANNING_INSTANCE_AMBIGUOUS", [f"plan.steps.{position}"],
                dict(operation_id=step.operation_id, role=step.role,
                     candidates=evidence,
                     message="模型没有提供足以唯一确定请求实例的已批准语义；"
                             "程序不会按数组位置或相似度猜测。"))
        unresolved = [item for item in unresolved if item[1].id not in mapping]
    return mapping, by_instance


def _request_signature(step):
    import json

    return json.dumps(dict(operation_id=step.operation_id, role=step.role,
                           policy_id=step.policy_id,
                           arguments=[argument.model_dump(mode="json")
                                      for argument in step.arguments]),
                      sort_keys=True, ensure_ascii=False, default=str)


def _policy_for(instance, policies):
    if instance.policy_id is None:
        raise _failure("PLANNING_POLICY_MISSING", [f"instance.{instance.instance_id}"])
    policy = policies.get(instance.policy_id)
    if policy is None:
        raise _failure("CONSTRUCTION_NODE_NOT_APPROVED", [f"instance.{instance.instance_id}"])
    if (policy.get("operation_id") != instance.operation_id
            or instance.role not in (policy.get("roles") or [])):
        raise _failure("CONSTRUCTION_NODE_NOT_APPROVED", [f"instance.{instance.instance_id}"])
    return policy


def _scalar(value):
    return value is None or type(value) in {str, int, float, bool}


def _apply_arguments(step, instance, mapping, instance_step, step_position):
    """Keep approved literals, replace data references with program-resolved producers.

    Execution step ids stay the proposal's ids; the program owns the *instance* identity behind
    each step, so exported evidence and existing execution expectations remain readable.
    """
    arguments = {}
    provided = {}
    for argument in step.arguments:
        data = argument.model_dump(mode="json")
        if data.get("from_step") is not None:
            target_instance = mapping.get(data["from_step"])
            target = instance_step.get(target_instance) if target_instance else None
            if target is None:
                raise _failure(
                    "PLANNING_REFERENCE_ORDER_INVALID",
                    [f"plan.steps.{step.id}.from_step"],
                    dict(step=step.id, from_step=data["from_step"],
                         message="数据引用指向本场景不存在的步骤。"))
            data["from_step"] = target
        else:
            data["select"] = None
        arguments[(data["location"], data["name"])] = data
        provided[(data["location"], data["name"])] = data
    for argument in instance.arguments:
        slot = (argument.location, argument.name)
        if argument.from_instance is not None:
            target = instance_step.get(argument.from_instance)
            if target is None:
                raise _failure(
                    "PLANNING_REFERENCE_ORDER_INVALID",
                    [f"instance.{instance.instance_id}.from_step"],
                    dict(instance=instance.instance_id,
                         from_instance=argument.from_instance))
            arguments[slot] = dict(location=argument.location, name=argument.name, value=None,
                                   from_step=target, select=argument.select)
        elif argument.value is not None and _scalar(argument.value):
            arguments[slot] = dict(location=argument.location, name=argument.name,
                                   value=argument.value, from_step=None, select=None)
    # A proposal that names a binding or a literal must agree with the approved one. Silently
    # substituting the approved value would turn "the model planned something else" into a pass
    # and hide exactly the defect the operator needs to see (P0: never replace unapproved data).
    for slot, data in provided.items():
        approved = next((item for item in instance.arguments
                         if (item.location, item.name) == slot), None)
        if approved is None:
            continue
        if approved.from_instance is not None:
            expected = instance_step.get(approved.from_instance)
            if data.get("from_step") != expected:
                raise _failure(
                    "PLANNING_REFERENCE_MISMATCH",
                    [f"plan.steps.{step.id}.from_step"],
                    dict(step=step.id, slot=list(slot), approved_from_step=expected,
                         proposed_from_step=data.get("from_step"),
                         message="步骤的数据来源与已批准策略不一致。"))
            if (data.get("select") is not None and approved.select is not None
                    and data["select"] != approved.select):
                raise _failure(
                    "PLANNING_REFERENCE_MISMATCH",
                    [f"plan.steps.{step.id}.select"],
                    dict(step=step.id, slot=list(slot), approved_select=approved.select,
                         proposed_select=data["select"],
                         message="步骤的取值路径与已批准策略不一致。"))
        elif approved.value is not None and data.get("value") is not None:
            if data["value"] != approved.value:
                raise _failure(
                    "DESIGN_LITERAL_NOT_APPROVED",
                    [f"plan.steps.{step.id}.value"],
                    dict(step=step.id, slot=list(slot),
                         message="步骤使用的字面量与已批准取值不一致。"))
    for data in arguments.values():
        if data.get("from_step") is None:
            continue
        target_position = step_position.get(data["from_step"])
        if target_position is None or target_position >= step_position[step.id]:
            raise _failure(
                "PLANNING_REFERENCE_ORDER_INVALID",
                [f"instance.{instance.instance_id}.from_step"],
                dict(instance=instance.instance_id, from_step=data["from_step"],
                     message="引用的来源实例不在本场景，或没有先于使用它的实例执行。"))
    return [arguments[key] for key in sorted(arguments)]


def can_compile(ir: PlanningIR, draft, policies) -> bool:
    """Whether this proposal is a candidate for IR compilation (no negative variant, etc.)."""
    if not ir.instances or any(ir.ambiguities):
        return False
    for step in draft.steps:
        policy = policies.get(step.policy_id)
        if policy is not None and policy.get("negative_test"):
            return False
    return True


def compile_design(ir, plan, context):
    """Compile one Design case into an executable plan for the IR instances it realizes."""
    if not isinstance(ir, PlanningIR):
        ir = PlanningIR.model_validate(ir)
    draft = GenericPlannerDraft.model_validate(plan)
    policies = {item["id"]: item for item in context.get("policies", [])}
    if any(ir.ambiguities):
        ambiguity = ir.ambiguities[0]
        raise _failure("PLANNING_IR_AMBIGUOUS", fields=list(ambiguity.fields),
                       construction=dict(ambiguity=ambiguity.model_dump(mode="json")))
    mapping, by_instance = _step_instance_map(ir, draft, policies)
    instance_step = {}
    for design_id, instance_id in mapping.items():
        if instance_id in instance_step:
            raise _failure("PLANNING_INSTANCE_CONFLICT", [f"plan.steps.{design_id}"],
                           dict(instance_id=instance_id,
                                steps=[instance_step[instance_id], design_id],
                                message="两个执行步骤不能声明为同一个请求实例。"))
        instance_step[instance_id] = design_id
    known = {item.instance_id for item in ir.instances}
    present = sorted((instance_id for instance_id in instance_step if instance_id in known),
                     key=lambda key: ir.instance(key).sort_key)
    if not present:
        raise _failure("PLANNING_INSTANCE_COVERAGE", construction=dict(covered=[]))
    edges = ir.edge_pairs()
    missing = sorted({(source, target) for (source, target), reasons in edges.items()
                      if target in present and source not in present
                      and any(reason.reason in HARD_EDGE_REASONS for reason in reasons)})
    if missing:
        fields = [f"instance.{source}" for source, _ in missing]
        raise _failure(
            "PLANNING_INSTANCE_COVERAGE", fields=fields,
            construction=dict(
                covered=present, missing=[source for source, _ in missing],
                consumers=sorted({target for _, target in missing}),
                operations={source: ir.instance(source).operation_id
                            for source, _ in missing},
                message="消费实例引用的产生者步骤不在本场景中。",
                hint="这些是数据/生命周期前置；要么补上产生者步骤，要么去掉消费它的步骤。"))
    order = [key for key in ir.order if key in present]
    step_position = {instance_step[key]: position
                     for position, key in enumerate(order)}
    steps = []
    for instance_id in order:
        instance = ir.instance(instance_id)
        design_id = instance_step[instance_id]
        twins = by_instance.get(instance_id, [])
        if len(twins) != 1:
            raise _failure("PLANNING_INSTANCE_CONFLICT", [f"instance.{instance_id}"],
                           dict(instance_id=instance_id, steps=twins,
                                message="一个请求实例必须恰好对应一个执行步骤。"))
        step = next(item for item in draft.steps if item.id == twins[0])
        if step.operation_id != instance.operation_id or step.role != instance.role:
            raise _failure("PLANNING_INSTANCE_CONFLICT", [f"plan.steps.{step.id}"],
                           dict(instance_id=instance_id, operation_id=step.operation_id,
                                role=step.role))
        _policy_for(instance, policies)
        # The compiler owns identity: an instance's clause occurrence comes from the IR, never
        # from a model-echoed default (a null index on every step used to collapse all of them
        # onto `request_1:0` and be rejected as a conflict). A derived post-delete read is a
        # lifecycle verification, not a clause action, so it carries no requirement slot and the
        # clause validator does not try to count it twice.
        synthesized = instance.origin == ABSENCE_ORIGIN
        steps.append(dict(id=design_id, operation_id=instance.operation_id,
                          role=instance.role, policy_id=instance.policy_id,
                          arguments=_apply_arguments(step, instance, mapping, instance_step,
                                                     step_position),
                          requirement_source_id=None if synthesized
                          else (step.requirement_source_id or instance.source_id),
                          requirement_action_index=None if synthesized
                          else (instance.action_index if step.requirement_action_index is None
                                else step.requirement_action_index)))
    dependencies = {instance_step[instance_id]: [] for instance_id in present}
    reasons = {}
    evidence = {}
    for (source, target), items in edges.items():
        source_id, target_id = instance_step.get(source), instance_step.get(target)
        if target_id in dependencies and source_id in dependencies:
            dependencies[target_id].append(source_id)
            reasons[f"{target_id}<={source_id}"] = "+".join(item.reason for item in items)
            evidence[f"{target_id}<={source_id}"] = [item.model_dump(mode="json")
                                                     for item in items]
    compiled = dict(name=draft.name, steps=steps)
    stats = dict(encoding=ENCODING, proposed_nodes=len(draft.steps),
                 constructed_nodes=len(steps), instances=present,
                 instance_steps=dict(instance_step),
                 dependency_edges=sum(len(value) for value in dependencies.values()),
                 node_dependencies={key: sorted(value)
                                    for key, value in dependencies.items()},
                 edge_reasons=reasons, edge_evidence=evidence)
    _insert_authentication(compiled, stats, context)
    return compiled, stats


def _insert_authentication(compiled, stats, context):
    """Insert only the approved login node a protected operation requires."""
    policies = {item["id"]: item for item in context.get("policies", [])}
    inserted = 0
    for auth in context.get("authentication", []) or []:
        protected = [node for node in compiled["steps"]
                     if node["role"] == auth["role"]
                     and node["operation_id"] in auth["before_operation_ids"]]
        if not protected or auth["login_operation_id"] is None:
            continue
        existing = [node for node in compiled["steps"]
                    if node["operation_id"] == auth["login_operation_id"]
                    and node["role"] == auth["role"]]
        if not existing:
            allowed = [policy for policy in policies.values()
                       if policy["operation_id"] == auth["login_operation_id"]
                       and auth["role"] in policy["roles"]
                       and not policy.get("negative_test")]
            if len(allowed) != 1:
                raise _failure("CONSTRUCTION_AUTHENTICATION_AMBIGUOUS")
            key = f"managed_auth_{auth['role']}"
            if any(node["id"] == key for node in compiled["steps"]):
                raise _failure("CONSTRUCTION_RESERVED_NODE_ID")
            node = dict(id=key, operation_id=auth["login_operation_id"],
                        role=auth["role"], policy_id=allowed[0]["id"], arguments=[])
            position = min(compiled["steps"].index(item) for item in protected)
            compiled["steps"].insert(position, node)
            stats["node_dependencies"][key] = []
            existing = [node]
            inserted += 1
        if len(existing) != 1:
            raise _failure("CONSTRUCTION_AUTHENTICATION_AMBIGUOUS")
        for node in protected:
            if node["id"] == existing[0]["id"]:
                continue
            stats["node_dependencies"].setdefault(node["id"], [])
            if existing[0]["id"] not in stats["node_dependencies"][node["id"]]:
                stats["node_dependencies"][node["id"]].append(existing[0]["id"])
                stats["edge_reasons"][f"{node['id']}<={existing[0]['id']}"] = "AUTHENTICATION"
                stats["edge_evidence"][f"{node['id']}<={existing[0]['id']}"] = [
                    dict(reason="AUTHENTICATION", evidence="已批准登录前置", source_id=None)]
                stats["dependency_edges"] += 1
    stats["managed_authentication_nodes"] = inserted
    stats["constructed_nodes"] = len(compiled["steps"])


def validate_instance_coverage(ir: PlanningIR, cases, *, allow_partial=False):
    """Every required instance must be planned somewhere; none may vanish silently.

    ``allow_partial`` is set only when the proposal explicitly leaves work to a question or a
    ``not_covered`` entry; that omission stays visible to the user instead of being executed.
    """
    if not isinstance(ir, PlanningIR) or not ir.instances or any(ir.ambiguities):
        return
    if allow_partial:
        return
    required: dict[tuple[str, str | None], list[str]] = {}
    for instance in ir.instances:
        required.setdefault((instance.operation_id, instance.role), []).append(
            instance.instance_id)
    planned: dict[tuple[str, str | None], int] = {}
    for case in cases:
        for step in (case.get("plan") or {}).get("steps", []):
            if not isinstance(step, dict):
                continue
            key = (step.get("operation_id"), step.get("role"))
            planned[key] = planned.get(key, 0) + 1
    missing = [instance_id for key, instances in required.items()
               if planned.get(key, 0) < len(instances) for instance_id in instances]
    if missing:
        raise _failure("PLANNING_INSTANCE_COVERAGE", fields=missing,
                       construction=dict(missing=missing,
                                         planned={f"{key[0]}:{key[1]}": count
                                                  for key, count in planned.items()},
                                         message="模型的方案没有覆盖原文要求的全部请求实例。"))
