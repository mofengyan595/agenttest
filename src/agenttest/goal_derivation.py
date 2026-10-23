"""Derive bounded goals from original text and approved relations, before any planner."""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from agenttest.discovery_schema import fields, witness
from agenttest.eval_v2.models import ExpectedArgument, ExpectedStep, Rubric
from agenttest.planner_input import validate_user_task
from agenttest.policies import NeedsReview, dependency_roles_match
from agenttest.project_goals import GoalProject
from agenttest.report import fingerprint
from agenttest.task_contract import TaskContract

# Deliberately closed grammar: leftover words are unverified business requirements.
FILLER = ("已授权的", "核心接口", "这个项目", "这个", "项目", "接口", "流程", "验证", "测试",
          "帮我", "请", "先", "再", "然后", "随后", "以及", "并且", "并", "和", "及", "的", "后",
          "补充", "补", "测")
PROJECT_PHRASES = ("已授权的核心接口", "项目核心接口", "项目全部接口", "所有已授权接口")


def _reference_text(text):
    """Normalize typography and identifier case, never user-supplied quoted input values."""
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(repr=False)
class DerivedGoal:
    project_identity: str
    user_task_sha256: str
    scope: Literal["single", "flow", "project"]
    requested_goals: list[str]
    task: TaskContract | None = None
    reason: str | None = None
    question: str | None = None
    input_defaults: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return dict(schema_version="agenttest.derived-goal.v1",
            project_identity=self.project_identity, user_task_sha256=self.user_task_sha256,
            scope=self.scope, requested_goals=self.requested_goals,
            status="READY" if self.task else "NEEDS_REVIEW", reason=self.reason,
            question=self.question,
            required_plan=public_goal_plan(self),
            required_steps=len(self.task.requirements.steps) if self.task else 0,
            http_requests=0, model_calls=0)


def public_goal_plan(goal):
    """Concrete reviewable constraints with hashed literals; never raw request bodies."""
    if goal.task is None:
        return []
    return [dict(key=s.key, operation_id=s.operation_id, role=s.role, policy_id=s.policy_id,
        arguments=[dict(location=a.location, name=a.name,
            **(dict(source=a.source, selector=a.selector) if a.source else
               dict(value_sha256=fingerprint(a.value)))) for a in s.arguments],
        before=[b for a, b in goal.task.requirements.before if a == s.key])
        for s in goal.task.requirements.steps]


def _recognize(project: GoalProject, text: str) -> tuple[str, list[str], str | None]:
    quoted = list(re.finditer(r'“([^“”]+)”|"([^"\n]+)"', text))
    if len(quoted) > 1:
        raise NeedsReview("INTENT_MULTIPLE_LITERAL_VALUES_UNSUPPORTED")
    literal = None
    if quoted:
        match = quoted[0]
        literal = match.group(1) or match.group(2)
        text = text[:match.start()] + text[match.end():]
    text = _reference_text(text)
    if any(p in text for p in PROJECT_PHRASES):
        residue = text
        for part in sorted((*PROJECT_PHRASES, *FILLER, *project.catalog.resource_aliases),
                           key=len, reverse=True):
            residue = residue.replace(_reference_text(part), "")
        if re.sub(r"[\s，。；、：:;(),!?！？]+", "", residue) or literal is not None:
            raise NeedsReview("INTENT_UNVERIFIED_BUSINESS_REQUIREMENT")
        return "project", [g.id for g in project.catalog.goals], None
    choices = [(alias, g.id) for g in project.catalog.goals for alias in g.aliases]
    # Exact operation/path is a lookup alternative, not permission to choose among policies.
    operations = {o.operation_id: o for o in project.contract.operations if o.operation_id}
    definitions = {p.id: p for p in project.registry.definitions()}
    for goal in project.catalog.goals:
        definition = definitions.get(goal.policy_id)
        op = operations.get(definition.operation_id) if definition else None
        if op is None:
            raise NeedsReview("INTENT_GOAL_POLICY_NOT_APPROVED")
        choices.extend([(op.operation_id, goal.id), (op.method + " " + op.path, goal.id)])
    choices = [(_reference_text(alias), key) for alias, key in choices]
    tokens = sorted({alias for alias, _ in choices}, key=lambda s: (-len(s), s))
    expression = re.compile("|".join(re.escape(token).replace(r"\ ", r"\s+") for token in tokens))
    found, residue, last = [], [], 0
    for match in expression.finditer(text):
        candidates = {key for alias, key in choices
                      if re.sub(r"\s+", " ", alias) == re.sub(r"\s+", " ", match.group())}
        if len(candidates) != 1:
            raise NeedsReview("INTENT_OPERATION_EXPECTATION_AMBIGUOUS")
        found.append(next(iter(candidates)))
        residue.append(text[last:match.start()])
        last = match.end()
    residue.append(text[last:])
    remaining = "".join(residue)
    for part in sorted((*project.catalog.resource_aliases, *FILLER), key=len, reverse=True):
        remaining = remaining.replace(_reference_text(part), "")
    if re.sub(r"[\s，。；、：:;(),!?！？]+", "", remaining) or not found:
        raise NeedsReview("INTENT_UNVERIFIED_BUSINESS_REQUIREMENT")
    if len(set(found)) != len(found):
        raise NeedsReview("INTENT_REPEATED_RESOURCE_OR_GOAL_UNSUPPORTED")
    return "single" if len(found) == 1 else "flow", found, literal


def derive_goal(project: GoalProject, user_task: str) -> DerivedGoal:
    """No plan argument exists: a planner cannot manufacture the acceptance target."""
    validate_user_task(user_task)
    result = DerivedGoal(project.identity(), fingerprint(user_task), "single", [])
    try:
        result.scope, result.requested_goals, literal = _recognize(project, user_task)
        if result.scope == "project":
            raise NeedsReview("INTENT_PROJECT_GROUPING_REQUIRED")
        _derive_requirements(project, user_task, result, literal)
    except NeedsReview as exc:
        result.reason = exc.diagnostic.code
        result.question = ("请明确要验证的接口行为及已批准的预期；当前需求中有无法核验的部分。"
            if "UNVERIFIED" in result.reason else
            "请确认所需业务预期、资源或角色；当前批准配置不足以唯一确定目标。")
    except ValueError:
        result.reason = "INTENT_GOAL_CONFIGURATION_UNSUPPORTED"
        result.question = "请核对项目的目标关系、输入规则和请求预算。"
    return result


def _derive_requirements(project, user_task, result, literal):
    policies = {p.id: p for p in project.registry.definitions()}
    operations = {o.operation_id: o for o in project.contract.operations if o.operation_id}
    templates = {g.id: g for g in project.catalog.goals}
    steps, built, active, defaults, before = [], {}, set(), [], []
    literal_slots = []

    def visit(policy_id, role):
        key = (policy_id, role)
        if key in active or len(steps) >= 20:
            raise NeedsReview("INTENT_GOAL_DEPENDENCY_CYCLE_OR_LIMIT")
        if key in built:
            return built[key]
        policy = policies.get(policy_id)
        if (policy is None or role not in policy.roles
                or policy.business_scenario != project.target.business_scenario
                or not policy.assertions.reviewed or not policy.assertions.source.strip()
                or not (policy.assertions.status or policy.assertions.fields)):
            raise NeedsReview("INTENT_GOAL_POLICY_NOT_APPROVED")
        op = operations.get(policy.operation_id)
        if op is None:
            raise NeedsReview("INTENT_GOAL_OPERATION_UNKNOWN")
        try:
            project.target.permit(op.operation_id, op.method)
        except ValueError:
            raise NeedsReview("INTENT_GOAL_OPERATION_NOT_PERMITTED") from None
        if policy.required_bindings or policy.poll or policy.csv_fixture:
            raise NeedsReview("INTENT_GOAL_EXECUTION_OPTIONS_UNSUPPORTED")
        active.add(key)
        credential = project.target.roles.get(role)
        is_login = credential and credential.login_operation == op.operation_id
        login = None
        protected = bool(op.security) and {} not in op.security
        if protected and not is_login:
            if credential is None:
                raise NeedsReview("INTENT_GOAL_ROLE_REQUIRED")
            if credential.login_operation:
                matches = [p for p in policies.values()
                    if p.operation_id == credential.login_operation and role in p.roles
                    and p.business_scenario == project.target.business_scenario]
                if len(matches) != 1:
                    raise NeedsReview("INTENT_GOAL_AUTHENTICATION_AMBIGUOUS")
                login = visit(matches[0].id, role)
        arguments = []
        specs = [] if is_login else fields(op, project.contract.version, "#/operations")[0]
        by_slot = {(f.location, f.name): f for f in specs}
        for rule in policy.binding_rules:
            if rule.source_policy_id:
                source = policies.get(rule.source_policy_id)
                if source is None:
                    raise NeedsReview("INTENT_GOAL_SOURCE_UNKNOWN")
                roles = [r for r in source.roles if (r is None or r in project.target.roles)
                    and dependency_roles_match(r, role, rule.role_relation)]
                preferred = {g.role for g in project.catalog.goals
                             if g.policy_id == source.id and g.role in roles}
                if len(roles) > 1 and len(preferred) == 1:
                    roles = list(preferred)
                if len(roles) != 1:
                    raise NeedsReview("INTENT_GOAL_SOURCE_ROLE_AMBIGUOUS")
                producer = visit(source.id, roles[0])
                arguments.append(ExpectedArgument(location=rule.location, name=rule.name,
                    source=producer.key, selector=rule.source_selector))
            elif rule.literal_schema is not None:
                spec = by_slot.get((rule.location, rule.name))
                if spec is None:
                    raise NeedsReview("INTENT_GOAL_ARGUMENT_UNKNOWN")
                value = witness(spec.schema, rule.literal_schema)
                argument = ExpectedArgument(location=rule.location, name=rule.name, value=value)
                arguments.append(argument)
                defaults.append(dict(policy_id=policy_id, location=rule.location,
                                     name=rule.name, value=value))
                if (rule.location == "body" and spec.schema.get("type") == "string"
                        and not rule.literal_schema.get("enum") and not spec.schema.get("enum")):
                    literal_slots.append((argument, defaults[-1], spec.schema, rule.literal_schema))
            else:
                raise NeedsReview("INTENT_GOAL_PARAMETER_NOT_DEFINED")
        present = {(a.location, a.name) for a in arguments}
        if any(f.required and (f.location, f.name) not in present for f in specs):
            raise NeedsReview("INTENT_GOAL_REQUIRED_INPUT_NOT_APPROVED")
        step = ExpectedStep(key=f"g{len(steps) + 1}", operation_id=op.operation_id,
            role=role, policy_id=policy_id, arguments=arguments,
            goals=[f"prerequisite:{policy_id}:{role}"])
        steps.append(step)
        built[key] = step
        active.remove(key)
        if login:
            before.append((login.key, step.key))
        return step

    roots, seen = [], set()
    for goal_id in result.requested_goals:
        goal = templates[goal_id]
        variants = [v.policy_id for v in goal.variants if v.after_goal in seen]
        if len(variants) > 1:
            raise NeedsReview("INTENT_GOAL_STATE_AMBIGUOUS")
        root = visit(variants[0] if variants else goal.policy_id, goal.role)
        root.goals.append("requested:" + goal_id)
        roots.append(root.key)
        seen.add(goal_id)
    before.extend(zip(roots, roots[1:]))
    if literal is not None:
        if len(literal_slots) != 1:
            raise NeedsReview("INTENT_LITERAL_FIELD_AMBIGUOUS")
        from agenttest.request_validation import validate_value

        argument, default, schema, constraint = literal_slots[0]
        validate_value(literal, schema)
        validate_value(literal, constraint)
        # ExpectedStep owns its validated copy; find the corresponding copied argument.
        for step in steps:
            for arg in step.arguments:
                if arg is argument:
                    arg.value = literal
        default["value"] = literal
    if len(steps) > min(project.target.max_steps, project.target.max_requests):
        raise NeedsReview("INTENT_GOAL_REQUEST_BUDGET_EXCEEDED")
    result.input_defaults = defaults
    result.task = TaskContract(user_task=user_task,
        contract_sha256=fingerprint(project.contract.model_dump()),
        target_config_sha256=fingerprint(project.target.model_dump()),
        policy_sha256=project.registry.digest(),
        requirements=Rubric(steps=steps, before=list(before),
            required_goals=sorted({g for s in steps for g in s.goals})))
