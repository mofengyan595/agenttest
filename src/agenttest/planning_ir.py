"""Normalized planning IR: action instances, constraints, resource handles and dependency edges.

The LLM decides *what* the user asked for (operations, statuses, resources, order). This module
decides *how many* requests that means, which clause is a request and which is a constraint, and
which instance produces a value another instance consumes. The result is an auditable graph whose
edges carry the evidence that created them; a model's array position never becomes an identity.

Only deterministic evidence is used: explicit HTTP statuses, request/verification verbs, sequence
markers, clause positions in the original text, approved policy bindings and the target's declared
resource lifecycle. Anything that cannot be decided from that evidence becomes an ``Ambiguity``;
it is never guessed and never widened to "all calls of this operation".
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClauseRole(str, Enum):
    """What one original-text clause contributes to the plan."""

    REQUEST = "REQUEST"
    ASSERTION = "ASSERTION"
    DATA_CAPTURE = "DATA_CAPTURE"
    CONSTRAINT = "CONSTRAINT"
    DEFERRED = "DEFERRED"
    UNCLASSIFIED = "UNCLASSIFIED"


class ResourcePhase(str, Enum):
    """Which lifecycle state a request needs, not merely which operation it calls."""

    EXISTS = "EXISTS"
    DELETED = "DELETED"
    VERIFY_ABSENT = "VERIFY_ABSENT"


class ConstraintKind(str, Enum):
    MUST_PRECEDE = "MUST_PRECEDE"
    SAME_RESOURCE = "SAME_RESOURCE"
    DIFFERENT_RESOURCE = "DIFFERENT_RESOURCE"
    RESPONSE_ASSERTION = "RESPONSE_ASSERTION"
    DATA_DEPENDENCY = "DATA_DEPENDENCY"
    RESOURCE_EXISTS = "RESOURCE_EXISTS"
    RESOURCE_ABSENT = "RESOURCE_ABSENT"
    RESOURCE_DELETED = "RESOURCE_DELETED"
    POLICY_PREREQUISITE = "POLICY_PREREQUISITE"


class AmbiguityKind(str, Enum):
    MISSING_INSTANCE_IDENTITY = "MISSING_INSTANCE_IDENTITY"
    UNCLASSIFIED_CLAUSE = "UNCLASSIFIED_CLAUSE"
    CONSTRAINT_CONFLICT = "CONSTRAINT_CONFLICT"
    RESOURCE_REFERENCE_AMBIGUOUS = "RESOURCE_REFERENCE_AMBIGUOUS"
    PARAMETER_SOURCE_UNKNOWN = "PARAMETER_SOURCE_UNKNOWN"
    OPERATION_NOT_APPROVED = "OPERATION_NOT_APPROVED"
    RESPONSE_STATUS_NOT_APPROVED = "RESPONSE_STATUS_NOT_APPROVED"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    INSTANCE_COVERAGE = "INSTANCE_COVERAGE"
    UNSOURCED_STEP = "UNSOURCED_STEP"
    REFERENCE_ORDER_INVALID = "REFERENCE_ORDER_INVALID"


class EDGE:
    """Edge reasons; they are kept as a set per ordered pair, never overwritten."""

    TEXT_ORDER = "TEXT_ORDER"
    MODEL_REFERENCE = "MODEL_REFERENCE"
    POLICY_PREREQUISITE = "POLICY_PREREQUISITE"
    DATA_DEPENDENCY = "DATA_DEPENDENCY"
    RESOURCE_EXISTS = "RESOURCE_EXISTS"
    LIFECYCLE_CLEANUP = "LIFECYCLE_CLEANUP"
    RESOURCE_ABSENT = "RESOURCE_ABSENT"
    REPLAY_GROUP = "REPLAY_GROUP"
    SEQUENCE_MARKER = "SEQUENCE_MARKER"


# Only these reasons make a producer *required* for its consumer to run. Ordering-only reasons
# ("the delete happens after whichever reads occurred") must not force a whole request into one
# case: that made create+delete impossible and pushed the model towards create-only designs.
HARD_EDGE_REASONS = frozenset({
    EDGE.DATA_DEPENDENCY,
    EDGE.POLICY_PREREQUISITE,
    EDGE.RESOURCE_EXISTS,
    EDGE.RESOURCE_ABSENT,
})

# The IR derives this instance from an approved 404/410 rule and a planned deletion. It is a
# lifecycle verification of an existing instance, not an action the user's clause named.
ABSENCE_INSTANCE_ORIGIN = "APPROVED_ABSENCE_POLICY"


class ResponseAssertion(BaseModel):
    """An expected status that belongs to a request instance, never to a new request."""

    statuses: list[int] = Field(min_length=1, max_length=30)
    source_id: str
    source_quote: str
    origin: str = "EXPLICIT_STATUS"


class InstanceArgument(BaseModel):
    """One argument slot resolved by the program (never by proposal array position)."""

    location: str
    name: str
    value: Any = None
    from_instance: str | None = None
    select: str = "$"
    origin: str = "POLICY_BINDING"


class ActionInstance(BaseModel):
    """One concrete business request the user asked for."""

    instance_id: str
    operation_id: str
    role: str | None
    source_id: str
    source_quote: str
    source_start: int
    action_index: int = 0
    policy_id: str | None = None
    policy_origin: str | None = None
    assertions: list[ResponseAssertion] = Field(default_factory=list)
    resource_id: str | None = None
    phase: str | None = None
    arguments: list[InstanceArgument] = Field(default_factory=list)
    origin: str = "FOCUS"

    @property
    def sort_key(self):
        return (self.source_start, self.action_index, self.instance_id)


class ResourceHandle(BaseModel):
    """A stable logical resource produced by one create instance."""

    resource_id: str
    producer_instance: str
    operation_id: str
    path_parameter: str
    selector: str


class ClauseRecord(BaseModel):
    """The program's classification of one original-text clause, with its evidence."""

    source_id: str
    text: str
    start: int
    role: ClauseRole
    detail: dict = Field(default_factory=dict)


class Constraint(BaseModel):
    """A rule that is not a request; several constraints may come from one clause."""

    kind: ConstraintKind
    source_id: str | None = None
    source_quote: str | None = None
    instances: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)
    origin: str = "FOCUS_TEXT"


class EdgeReason(BaseModel):
    """Why one ordered dependency exists and where its evidence came from."""

    reason: str
    evidence: str
    source_id: str | None = None


class DependencyEdge(BaseModel):
    """``target_instance`` must run after ``source_instance``."""

    source_instance: str
    target_instance: str
    reasons: list[EdgeReason] = Field(default_factory=list)


class Ambiguity(BaseModel):
    """A planning question the program refuses to answer by guessing."""

    kind: AmbiguityKind
    message: str
    source_id: str | None = None
    fields: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)


class PlanningIR(BaseModel):
    """Normalized planning representation, compiled before any Design or HTTP work."""

    instances: list[ActionInstance]
    resources: list[ResourceHandle] = Field(default_factory=list)
    clauses: list[ClauseRecord] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    normalized_requirements: list[dict] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    normalization_applied: bool = False
    order: list[str] = Field(default_factory=list)

    def instance(self, instance_id: str) -> ActionInstance:
        return next(item for item in self.instances if item.instance_id == instance_id)

    def instances_for(self, source_id: str, index: int = 0) -> list[str]:
        return [item.instance_id for item in self.instances
                if item.source_id == source_id and item.action_index == index]

    def edge_pairs(self) -> dict[tuple[str, str], list[EdgeReason]]:
        pairs: dict[tuple[str, str], list[EdgeReason]] = {}
        for edge in self.edges:
            pairs.setdefault((edge.source_instance, edge.target_instance), []).extend(
                edge.reasons)
        return pairs

    def public_summary(self) -> dict:
        """Report-safe projection: what was decided, why, and what stayed ambiguous."""
        return dict(
            encoding="NORMALIZED_PLANNING_IR_V1",
            normalization_applied=self.normalization_applied,
            coverage=self.coverage,
            clauses=[item.model_dump(mode="json") for item in self.clauses],
            instances=[dict(instance_id=item.instance_id, operation_id=item.operation_id,
                            role=item.role, source_id=item.source_id,
                            source_quote=item.source_quote, action_index=item.action_index,
                            policy_id=item.policy_id, policy_origin=item.policy_origin,
                            assertions=[assertion.model_dump(mode="json")
                                        for assertion in item.assertions],
                            resource_id=item.resource_id, phase=item.phase,
                            arguments=[argument.model_dump(mode="json")
                                       for argument in item.arguments])
                       for item in self.instances],
            resources=[item.model_dump(mode="json") for item in self.resources],
            constraints=[item.model_dump(mode="json") for item in self.constraints],
            edges=[item.model_dump(mode="json") for item in self.edges],
            ambiguities=[item.model_dump(mode="json") for item in self.ambiguities],
            order=self.order,
        )

    def scope_to(self, operations, *, source_ids=None) -> "PlanningIR":
        """The same representation narrowed to one group, with its dependency closure.

        A later round owns one obligation, not the whole request. Narrowing must not be done by
        discarding the IR (that silently changes the planning engine): the group keeps its
        instances, resources, edges and constraints. Instances the group *depends on* are kept
        even when their operation is outside the obligation -- a follow-up read of a deleted
        resource is meaningless without the create and delete that establish that phase, and
        dropping them is what made the P0 rounds redesign the precondition from text.
        """
        keep = set(operations)
        seed = {item.instance_id for item in self.instances
                if item.operation_id in keep
                and (source_ids is None or item.source_id in set(source_ids))}
        # Dependency closure: resource producers and argument sources of what is in scope.
        by_id = {item.instance_id: item for item in self.instances}
        closure = set(seed)
        changed = True
        while changed:
            changed = False
            for instance_id in list(closure):
                item = by_id.get(instance_id)
                if item is None:
                    continue
                needed = {argument.from_instance for argument in item.arguments
                          if argument.from_instance} | {resource.producer_instance
                            for resource in self.resources
                            if resource.producer_instance in by_id
                            and self._resource_feeds(resource, instance_id)}
                for needed_id in needed - closure:
                    if needed_id in by_id:
                        closure.add(needed_id)
                        changed = True
            for edge in self.edges:
                if edge.target_instance not in closure or edge.source_instance in closure:
                    continue
                if any(reason.reason in HARD_EDGE_REASONS for reason in edge.reasons):
                    closure.add(edge.source_instance)
                    changed = True
        instances = [item for item in self.instances if item.instance_id in closure]
        instance_ids = {item.instance_id for item in instances}
        # Preconditions live in these three lists; carrying them is what lets the scoped
        # round reason about "delete then 404" instead of designing a fresh plan from text.
        resources = [item for item in self.resources
                     if item.producer_instance in instance_ids]
        edges = [item for item in self.edges
                 if item.source_instance in instance_ids and item.target_instance in instance_ids]
        constraints = [item for item in self.constraints
                       if not item.instances or set(item.instances) & instance_ids]
        source_scope = {item.source_id for item in instances}
        clauses = [item for item in self.clauses if item.source_id in source_scope]
        kept_ids = {item.instance_id for item in instances}
        kept_operations = {item.operation_id for item in instances}
        return self.model_copy(update=dict(
            instances=instances, resources=resources, edges=edges, constraints=constraints,
            clauses=clauses,
            normalized_requirements=[item for item in self.normalized_requirements
                                     if item.get("source_id") in source_scope],
            ambiguities=[item for item in self.ambiguities
                         if item.source_id is None or item.source_id in source_scope],
            # ``order`` holds instance ids (an older IR may hold operation ids); filter by both
            # so a scoped round never compiles an empty plan.
            order=[item for item in self.order if item in kept_ids or item in kept_operations]))

    def _resource_feeds(self, resource: "ResourceHandle", instance_id: str) -> bool:
        """True when this instance consumes the resource a producer instance creates."""
        target = next((item for item in self.instances if item.instance_id == instance_id), None)
        if target is None:
            return False
        consumed = {argument.from_instance for argument in target.arguments}
        producer = resource.producer_instance
        return producer in consumed or any(
            edge.target_instance == instance_id and edge.source_instance == producer
            for edge in self.edges)


# --------------------------------------------------------------------------------------
# Deterministic clause evidence
# --------------------------------------------------------------------------------------

REQUEST_VERB = re.compile(
    r"创建|新建|提交|上传|更新|修改|替换|读取|读回|取回|回读|查询|查回|列出|获取|删除|"
    r"写入|导出|执行|注册|登录|发布|下单|支付|调用|请求|发起|改成|改为|变更")
VERIFY_VERB = re.compile(r"确认|验证|检查|核对|校验|断言|确保")
READ_VERB = re.compile(r"读取|读回|取回|回读|查询|查看|查回|查一下|再查|是否还存在|不存在")
WRITE_VERB = re.compile(r"创建|新建|提交|上传|更新|修改|替换|删除|写入|导出|"
                        r"注册|登录|发布|下单|支付|改成|改为|变更")
STATUS = re.compile(r"(?<!\d)([1-5]\d{2})(?!\d)")
DATA_CAPTURE = re.compile(
    r"(?:响应|返回|结果|应答|回复).{0,8}(?:id|ID|Id|标识|编号)"
    r"|(?:拿到|取得|取下|记下|记录|保存|提取|获取).{0,10}(?:id|ID|Id|标识|编号|返回值|响应)"
    r"|(?:id|ID|Id).{0,6}(?:返回|响应)")
SEQUENCE_MARKER = re.compile(
    r"^\s*(?:先|首先|接着|随后|然后|再|之后|最后|其后|next|then|finally|after\s+that)",
    re.IGNORECASE)


def explicit_statuses(text: str) -> list[int]:
    """HTTP status numbers written in a clause, in text order, without duplicates."""
    seen: list[int] = []
    for match in STATUS.finditer(text or ""):
        code = int(match.group(1))
        if code not in seen:
            seen.append(code)
    return seen


def request_verbs(text: str) -> list[str]:
    return REQUEST_VERB.findall(text or "")


def verification_only(text: str) -> bool:
    """A clause that only confirms/verifies something, without requesting a new call."""
    return bool(VERIFY_VERB.search(text or "")) and not request_verbs(text)


def data_capture(text: str) -> bool:
    return bool(DATA_CAPTURE.search(text or ""))


def starts_with_sequence_marker(text: str) -> bool:
    return bool(SEQUENCE_MARKER.match(text or ""))


def _policy_candidates(policies, operation_id, role):
    """Approved non-negative policies for one operation and role."""
    return [policy for policy in policies
            if policy.get("operation_id") == operation_id
            and role in (policy.get("roles") or [])
            and policy.get("negative") is None]


def _is_expectation(policy) -> bool:
    source = ((policy.get("assertions") or {}).get("source") or "")
    if ("_expectation_" in policy.get("id", "")
            or source.endswith("OPERATOR_EXPECTATION_RULE")
            or source.endswith("OPERATOR_ACCESS_RULE")):
        return True
    # An approved 404/410 is an *absence expectation*, never the default success criterion of
    # its operation. Without this an injected `<read>_absence404` policy made every ordinary
    # read ambiguous ("two approved policies, no status stated") and blocked the whole IR.
    codes = _status_codes(policy)
    return bool(codes) and set(codes) <= {404, 410}


def _status_codes(policy):
    status = (policy.get("assertions") or {}).get("status") or {}
    return list(status.get("codes") or [])


def _add_edge(edges, source, target, reason, evidence, source_id=None):
    """Record one ordered dependency and every reason behind it."""
    if source == target:
        return
    reasons = edges.setdefault((source, target), [])
    if all(item.reason != reason for item in reasons):
        reasons.append(EdgeReason(reason=reason, evidence=evidence, source_id=source_id))


def _cycle_path(instance_ids, predecessors):
    """One concrete cycle among the given instances, as an ordered node list."""
    pending = set(instance_ids)
    start = sorted(pending)[0]
    trail: list[str] = []

    def walk(key, seen):
        trail.append(key)
        for predecessor in sorted(predecessors.get(key, set()) & pending):
            if predecessor in trail:
                return trail[trail.index(predecessor):]
            if predecessor in seen:
                continue
            seen.add(predecessor)
            found = walk(predecessor, seen)
            if found:
                return found
        trail.pop()
        return None

    return walk(start, {start}) or [start]


def topological_order(instances, edge_pairs):
    """Stable order from the graph; returns ``(order, cycle)`` and never repairs a cycle."""
    by_id = {item.instance_id: item for item in instances}
    predecessors: dict[str, set[str]] = {key: set() for key in by_id}
    for source, target in edge_pairs:
        if source in by_id and target in by_id:
            predecessors[target].add(source)
    pending = set(by_id)
    order: list[str] = []
    while pending:
        ready = sorted((key for key in pending if not predecessors[key] & pending),
                       key=lambda key: by_id[key].sort_key)
        if not ready:
            return order, _cycle_path(pending, predecessors)
        for key in ready:
            order.append(key)
            pending.remove(key)
    return order, None


# --------------------------------------------------------------------------------------
# IR construction
# --------------------------------------------------------------------------------------

def build_planning_ir(requirements, atoms, *, operations, policies,
                      target_lifecycles=(), read_operations=(), input_requirements=()):
    """Normalize Focus clauses into instances and constraints without any model call.

    ``requirements`` are the validated ``RequestRequirement`` dumps of one request;
    ``atoms`` are the same request's original-text spans. The returned IR is complete enough
    to reject an unsatisfiable request before a Design call and to compile the executable
    order after one.
    """
    operations = {item["id"]: item for item in operations}
    atoms = sorted(atoms, key=lambda atom: atom.get("start", 0))
    clauses: list[ClauseRecord] = []
    instances: list[ActionInstance] = []
    constraints: list[Constraint] = []
    ambiguities: list[Ambiguity] = []
    advisories: list[dict] = []
    edges: dict[tuple[str, str], list[EdgeReason]] = {}
    by_source = {item["source_id"]: item for item in requirements}
    read_ops = set(read_operations or ())
    raw_required = [item for item in requirements if item.get("disposition") == "required"]
    seen_operations: set[str] = set()
    input_quotes = {(item.get("source_quote") or "").strip()
                    for item in input_requirements or ()}

    # 1. Classify every clause and create request instances in original-text order.
    for atom in atoms:
        requirement = by_source.get(atom["source_id"])
        if requirement is None:
            continue
        text = atom.get("text", "")
        role = _clause_role(atom, requirement, seen_operations, input_quotes, read_ops)
        statuses = explicit_statuses(text)
        clauses.append(ClauseRecord(
            source_id=atom["source_id"], text=text, start=atom.get("start", 0), role=role,
            detail=dict(actions=[action["operation_id"]
                                 for action in requirement.get("actions") or []],
                        statuses=statuses, request_verbs=request_verbs(text),
                        data_capture=data_capture(text),
                        sequence_marker=starts_with_sequence_marker(text))))
        if role is ClauseRole.UNCLASSIFIED:
            actions = [action.get("operation_id") for action in requirement.get("actions") or []]
            if actions and all(action in operations for action in actions):
                # The text does not read like a fresh request, but the model named only approved
                # operations for it. Refusing to plan would send the whole round to a human while
                # the legacy path would happily execute the same actions; instead the clause is
                # planned and the classification doubt is recorded as an advisory.
                role = ClauseRole.REQUEST
                clauses[-1] = clauses[-1].model_copy(update=dict(role=role))
                advisories.append(dict(code="UNCLASSIFIED_CLAUSE_PLANNED",
                                       source_id=atom["source_id"], actions=actions,
                                       reason="ACTIONS_ARE_APPROVED_OPERATIONS"))
            else:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.UNCLASSIFIED_CLAUSE, source_id=atom["source_id"],
                    message="该子句既不像新的请求，也不像状态/数据约束；不能据此凭空创建请求。",
                    fields=[f"requirement_map.{atom['source_id']}"]))
                continue
        if role is not ClauseRole.REQUEST:
            continue
        actions = requirement.get("actions") or []
        seen_operations.update(action["operation_id"] for action in actions)
        repeats = 1
        if len(actions) == 1 and actions[0]["operation_id"] in read_ops:
            from agenttest.request_requirements import explicit_read_repetitions

            repeats = explicit_read_repetitions(requirement, read_ops) or 1
        if len(actions) > 1:
            operation_ids = [action["operation_id"] for action in actions]
            if len(set(operation_ids)) != len(operation_ids):
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY, source_id=atom["source_id"],
                    message="同一子句重复要求同一接口，程序无法确认调用次数与实例身份。",
                    fields=[f"requirement_map.{atom['source_id']}.actions"]))
        for index, action in enumerate(actions):
            operation_id = action["operation_id"]
            if operation_id not in operations:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.OPERATION_NOT_APPROVED, source_id=atom["source_id"],
                    message=f"子句要求的接口 {operation_id} 不在已批准范围内。",
                    fields=[f"requirement_map.{atom['source_id']}"]))
                continue
            occurrences = repeats if len(actions) == 1 else 1
            for repeat in range(occurrences):
                instances.append(ActionInstance(
                    instance_id=f"ai_{len(instances) + 1}",
                    operation_id=operation_id, role=action.get("role"),
                    source_id=atom["source_id"], source_quote=text,
                    source_start=atom.get("start", 0), action_index=index + repeat,
                    origin="FOCUS_REPEAT" if repeat else "FOCUS"))
        if repeats > 1:
            same_clause = [item.instance_id for item in instances
                           if item.source_id == atom["source_id"]]
            for previous, current in zip(same_clause, same_clause[1:]):
                constraints.append(Constraint(
                    kind=ConstraintKind.MUST_PRECEDE, source_id=atom["source_id"],
                    source_quote=text, instances=[previous, current],
                    detail=dict(reason=EDGE.REPLAY_GROUP, count=repeats),
                    origin="DERIVED_REPETITION"))
                _add_edge(edges, previous, current, EDGE.REPLAY_GROUP,
                          f"{atom['source_id']} 明确要求重复调用", atom["source_id"])

    # 1b. A confirmed deletion may be verified through an approved absence policy. The
    # confirmation itself is not a second DELETE; when the target publishes a 404 read-back
    # rule, derive the missing read instance so Design can realize the check instead of
    # inventing an unbound step. The clause stays a CONSTRAINT; the returned call is a real
    # approved instance and is covered only when the proposal plans it.
    for clause in clauses:
        requirement = by_source.get(clause.source_id) or {}
        if clause.role is not ClauseRole.CONSTRAINT:
            continue
        if (not VERIFY_VERB.search(clause.text) or not re.search(r"删除|移除|清理", clause.text)
                or READ_VERB.search(clause.text)):
            continue
        actions = requirement.get("actions") or []
        if any(action["operation_id"] in read_ops for action in actions):
            continue
        deletes = [item for item in instances
                   if item.operation_id in {rule["delete_operation"]
                                            for rule in target_lifecycles}
                   and item.source_start < clause.start]
        if not deletes:
            continue
        deletion = deletes[-1]
        read_policy = next((policy for policy in policies
                            if policy["operation_id"] in read_ops
                            and deletion.role in (policy.get("roles") or [])
                            and set(_status_codes(policy)) == {404}), None)
        if read_policy is None:
            continue
        instances.append(ActionInstance(
            instance_id=f"ai_{len(instances) + 1}",
            operation_id=read_policy["operation_id"], role=deletion.role,
            source_id=clause.source_id, source_quote=clause.text,
            source_start=clause.start, action_index=len(actions),
            policy_id=read_policy["id"], origin="APPROVED_EXPECTATION",
            assertions=[ResponseAssertion(statuses=[404], source_id=clause.source_id,
                                          source_quote=clause.text)]))

    # 2. Attach explicit statuses to concrete instances, never to a new request.
    request_clauses = {clause.source_id for clause in clauses if clause.role is ClauseRole.REQUEST}
    for clause in clauses:
        if clause.role is ClauseRole.REQUEST:
            # A status written inside a request clause belongs to that clause's instances.
            statuses = clause.detail.get("statuses") or []
            if statuses:
                targets = [item for item in instances if item.source_id == clause.source_id]
                if len(targets) > 1:
                    ambiguities.append(Ambiguity(
                        kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY,
                        source_id=clause.source_id,
                        message="同一子句内的多个请求都带同一状态断言，无法确定它属于哪一个实例。"))
                for target in targets:
                    _attach_assertion(target, statuses, clause, constraints)
            continue
        if clause.role is ClauseRole.ASSERTION:
            statuses = clause.detail.get("statuses") or []
            if not statuses:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY, source_id=clause.source_id,
                    message="该子句在确认响应，但没有写明可执行的状态或字段断言。",
                    fields=[f"requirement_map.{clause.source_id}"]))
                continue
            anchor = _assertion_anchor(clause, instances, by_source)
            if anchor is None:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY, source_id=clause.source_id,
                    message="该状态断言之前没有可归属的请求实例。",
                    fields=[f"requirement_map.{clause.source_id}"]))
                continue
            _attach_assertion(anchor, statuses, clause, constraints)
        elif clause.role is ClauseRole.DATA_CAPTURE:
            anchor = _previous_request(clause, instances)
            if anchor is None:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.PARAMETER_SOURCE_UNKNOWN, source_id=clause.source_id,
                    message="该数据捕获子句之前没有可读取响应的请求实例。"))
                continue
            constraints.append(Constraint(
                kind=ConstraintKind.DATA_DEPENDENCY, source_id=clause.source_id,
                source_quote=clause.text, instances=[anchor.instance_id],
                detail=dict(captured=True, anchor=anchor.instance_id),
                origin="FOCUS_TEXT"))

    # 3. Resource handles from the target's declared lifecycle; never from operation names.
    resources: list[ResourceHandle] = []
    consumers_by_handle: dict[str, list[ActionInstance]] = {}
    for rule in target_lifecycles:
        creates = [item for item in instances
                   if item.operation_id == rule["create_operation"]]
        if not creates:
            continue
        if len(creates) > 1:
            ambiguities.append(Ambiguity(
                kind=AmbiguityKind.RESOURCE_REFERENCE_AMBIGUOUS,
                message="本轮创建了多个资源，但没有证据说明后续请求操作哪一个。",
                detail=dict(creates=[item.instance_id for item in creates])))
            continue
        producer = creates[0]
        handle = ResourceHandle(
            resource_id=f"resource_{len(resources) + 1}",
            producer_instance=producer.instance_id,
            operation_id=rule["create_operation"],
            path_parameter=rule["path_parameter"], selector=rule["id_selector"])
        resources.append(handle)
        producer.resource_id = handle.resource_id
        consumers = [item for item in instances
                     if _consumes_resource(item, producer, operations, rule)]
        consumers_by_handle[handle.resource_id] = consumers
        for consumer in consumers:
            consumer.resource_id = handle.resource_id
            consumer.phase = (ResourcePhase.DELETED.value
                              if consumer.operation_id == rule["delete_operation"]
                              else _phase_for(consumer))
            constraints.append(Constraint(
                kind=ConstraintKind.SAME_RESOURCE, source_id=consumer.source_id,
                source_quote=consumer.source_quote,
                instances=[producer.instance_id, consumer.instance_id],
                detail=dict(resource_id=handle.resource_id, phase=consumer.phase),
                origin="TARGET_LIFECYCLE"))
            bound = InstanceArgument(
                location="path_parameters", name=rule["path_parameter"], value=None,
                from_instance=producer.instance_id, select=rule["id_selector"],
                origin="RESOURCE_HANDLE")
            consumer.arguments = [argument for argument in consumer.arguments
                                  if (argument.location, argument.name)
                                  != (bound.location, bound.name)] + [bound]
            constraints.append(Constraint(
                kind=ConstraintKind.DATA_DEPENDENCY, source_id=consumer.source_id,
                source_quote=consumer.source_quote,
                instances=[consumer.instance_id, producer.instance_id],
                detail=dict(location=bound.location, name=bound.name,
                            select=bound.select, producer=producer.instance_id),
                origin="TARGET_LIFECYCLE"))
            _add_edge(edges, producer.instance_id, consumer.instance_id,
                      EDGE.DATA_DEPENDENCY,
                      f"{consumer.source_id} 消费 {producer.instance_id} 产生的 "
                      f"{rule['id_selector']}", consumer.source_id)

    # 3b. An approved absence rule is an expectation the operator already confirmed. Derive the
    # post-delete read instance from that policy instead of waiting for the request text to say
    # "verify it is gone": a narrowed round then has a real instance to bind, and the compiler
    # keeps the delete->readback dependency as a hard RESOURCE_ABSENT edge.
    for handle in resources:
        consumers = consumers_by_handle.get(handle.resource_id, [])
        if any(item.phase == ResourcePhase.VERIFY_ABSENT.value for item in consumers):
            continue
        deletes = [item for item in consumers if item.phase == ResourcePhase.DELETED.value]
        if not deletes:
            continue
        deletion = deletes[0]
        absence = _approved_absence_policy(policies, read_ops, deletion)
        if absence is None:
            continue
        # The request may already name the post-delete read ("create, delete, then read"): that
        # existing read instance *is* the absence verification. Deriving a second one would put
        # the same read both after and before the deletion and manufacture a dependency cycle.
        orders = {(order.get("earlier"), order.get("later"))
                  for order in (by_source.get(deletion.source_id) or {}).get("before") or ()}
        candidates = [item for item in consumers
                      if item.instance_id != deletion.instance_id
                      and item.operation_id == absence["operation_id"]
                      and item.source_id == deletion.source_id
                      and item.phase in (None, ResourcePhase.EXISTS.value)
                      and (deletion.operation_id, item.operation_id) in orders]
        reused = candidates[0] if len(candidates) == 1 else None
        if reused is not None:
            absent = reused
            absent.phase = ResourcePhase.VERIFY_ABSENT.value
            absent.policy_id = absence["id"]
            absent.policy_origin = "APPROVED_ABSENCE"
            absent.origin = ABSENCE_INSTANCE_ORIGIN
            absent.assertions = [ResponseAssertion(
                statuses=sorted(_status_codes(absence)), source_id=deletion.source_id,
                source_quote=deletion.source_quote, origin="APPROVED_ABSENCE")]
        else:
            absent = ActionInstance(
                instance_id=f"ai_{len(instances) + 1}",
                operation_id=absence["operation_id"], role=deletion.role,
                source_id=deletion.source_id, source_quote=deletion.source_quote,
                source_start=deletion.source_start, action_index=deletion.action_index,
                policy_id=absence["id"], policy_origin="APPROVED_ABSENCE",
                resource_id=handle.resource_id, phase=ResourcePhase.VERIFY_ABSENT.value,
                assertions=[ResponseAssertion(statuses=sorted(_status_codes(absence)),
                                              source_id=deletion.source_id,
                                              source_quote=deletion.source_quote,
                                              origin="APPROVED_ABSENCE")],
                origin="APPROVED_ABSENCE_POLICY")
            instances.append(absent)
            consumers.append(absent)
        if not any(argument.location == "path_parameters"
                   and argument.name == handle.path_parameter
                   for argument in absent.arguments):
            absent.arguments.append(InstanceArgument(
                location="path_parameters", name=handle.path_parameter, value=None,
                from_instance=handle.producer_instance, select=handle.selector,
                origin="RESOURCE_HANDLE"))
        constraints.append(Constraint(
            kind=ConstraintKind.SAME_RESOURCE, source_id=deletion.source_id,
            source_quote=deletion.source_quote,
            instances=[handle.producer_instance, absent.instance_id],
            detail=dict(resource_id=handle.resource_id,
                        phase=ResourcePhase.VERIFY_ABSENT.value),
            origin="APPROVED_ABSENCE"))
        constraints.append(Constraint(
            kind=ConstraintKind.DATA_DEPENDENCY, source_id=deletion.source_id,
            source_quote=deletion.source_quote,
            instances=[absent.instance_id, handle.producer_instance],
            detail=dict(location="path_parameters", name=handle.path_parameter,
                        select=handle.selector, producer=handle.producer_instance),
            origin="APPROVED_ABSENCE"))
        _add_edge(edges, handle.producer_instance, absent.instance_id,
                  EDGE.DATA_DEPENDENCY,
                  f"删除后回读沿用 {handle.producer_instance} 产生的 {handle.selector}",
                  deletion.source_id)

    # 4. Lifecycle edges by resource phase; the post-delete readback never delays the delete.
    for handle in resources:
        consumers = consumers_by_handle.get(handle.resource_id, [])
        exists = [item for item in consumers if item.phase == ResourcePhase.EXISTS.value]
        deletes = [item for item in consumers if item.phase == ResourcePhase.DELETED.value]
        absent = [item for item in consumers
                  if item.phase == ResourcePhase.VERIFY_ABSENT.value]
        if len(deletes) > 1:
            ambiguities.append(Ambiguity(
                kind=AmbiguityKind.CONSTRAINT_CONFLICT,
                message="同一资源被要求删除多次，计划存在真实冲突。",
                detail=dict(deletes=[item.instance_id for item in deletes])))
        for deletion in deletes:
            _add_edge(edges, handle.producer_instance, deletion.instance_id,
                      EDGE.RESOURCE_EXISTS, "删除前资源必须存在", deletion.source_id)
            for consumer in exists:
                _add_edge(edges, consumer.instance_id, deletion.instance_id,
                          EDGE.LIFECYCLE_CLEANUP, "删除发生在读取之后", deletion.source_id)
            for consumer in absent:
                _add_edge(edges, deletion.instance_id, consumer.instance_id,
                          EDGE.RESOURCE_ABSENT, "删除后回读验证资源不存在",
                          consumer.source_id)
        if absent and not deletes:
            ambiguities.append(Ambiguity(
                kind=AmbiguityKind.CONSTRAINT_CONFLICT,
                message="要求验证资源不存在，但本轮没有删除该资源。",
                detail=dict(absent=[item.instance_id for item in absent])))

    # 5. Written order from Focus, resolved to instances only.
    for clause in clauses:
        requirement = by_source.get(clause.source_id) or {}
        if requirement.get("disposition") != "required":
            continue
        if not any(item.source_id == clause.source_id for item in instances):
            # A clause that produced no instance (constraint, deferred, or refused request) has
            # nothing this round can order; its adjacency must not block the real requests.
            continue
        for order in requirement.get("before") or []:
            earlier = _order_side(order, "earlier", instances, clauses)
            later = _order_side(order, "later", instances, clauses)
            if earlier is None or later is None:
                # A confirmation such as "再确认" is a constraint, not a request. Focus may
                # still attach an adjacency edge from that clause; it has no executable
                # instance to order, so it must not block the real requests around it.
                missing = [side for side, resolved in (("earlier", earlier), ("later", later))
                           if resolved is None]
                if missing and all(_non_request_order_side(order, side, clauses)
                                   for side in missing):
                    continue
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY, source_id=clause.source_id,
                    message="顺序约束指向的子句没有对应的请求实例。",
                    fields=[f"requirement_map.{clause.source_id}.before"],
                    detail=dict(order=order)))
                continue
            for previous in earlier:
                for current in later:
                    if previous.instance_id == current.instance_id:
                        continue
                    _add_edge(edges, previous.instance_id, current.instance_id,
                              EDGE.TEXT_ORDER,
                              f"{clause.source_id} 规定 {previous.source_id} 先于 "
                              f"{current.source_id}", clause.source_id)
                    constraints.append(Constraint(
                        kind=ConstraintKind.MUST_PRECEDE, source_id=clause.source_id,
                        source_quote=clause.text,
                        instances=[previous.instance_id, current.instance_id],
                        detail=dict(reason=EDGE.TEXT_ORDER), origin="FOCUS_TEXT"))

    # 6. Adjacency markers only between consecutive request instances; no marker, no edge.
    requests_in_order = sorted(instances, key=lambda item: item.sort_key)
    for previous, current in zip(requests_in_order, requests_in_order[1:]):
        if previous.source_id == current.source_id:
            continue
        if not starts_with_sequence_marker(current.source_quote):
            continue
        _add_edge(edges, previous.instance_id, current.instance_id, EDGE.SEQUENCE_MARKER,
                  f"{current.source_id} 以顺序词开始，承接前一个请求", current.source_id)
        constraints.append(Constraint(
            kind=ConstraintKind.MUST_PRECEDE, source_id=current.source_id,
            source_quote=current.source_quote,
            instances=[previous.instance_id, current.instance_id],
            detail=dict(reason=EDGE.SEQUENCE_MARKER), origin="DERIVED_MARKER"))

    # 7. Approved policies, their binding rules and their prerequisites.
    input_by_slot = {}
    for item in input_requirements or ():
        input_by_slot.setdefault(
            (item.get("operation_id"), item.get("location"), item.get("name")),
            item.get("value"))
    for instance in instances:
        _select_policy(instance, policies, ambiguities)
    policy_instances: dict[str, list[str]] = {}
    for instance in instances:
        if instance.policy_id:
            policy_instances.setdefault(instance.policy_id, []).append(instance.instance_id)
    for instance in instances:
        if not instance.policy_id:
            continue
        chosen = next(policy for policy in policies if policy["id"] == instance.policy_id)
        _resolve_binding_arguments(instance, chosen, policy_instances, input_by_slot,
                                   ambiguities)
        for prerequisite in chosen.get("prerequisite_policy_ids") or []:
            producers = policy_instances.get(prerequisite, [])
            if len(producers) != 1:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY,
                    source_id=instance.source_id,
                    message="策略前置条件没有唯一的产生者实例。",
                    detail=dict(prerequisite=prerequisite, producers=producers)))
                continue
            _add_edge(edges, producers[0], instance.instance_id,
                      EDGE.POLICY_PREREQUISITE, f"已批准策略前置 {prerequisite}",
                      instance.source_id)

    # 3c. A policy-bound argument is a data dependency. Bindings are resolved in step 7, so this
    # pass runs after it; without the edge the compiler saw "create" and "read ?id from create"
    # as separate scenarios and rejected the read as DEPENDENCY_FORWARD_OR_UNKNOWN.
    known = {item.instance_id for item in instances}
    for consumer in instances:
        for argument in consumer.arguments:
            producer = argument.from_instance
            if producer is None or producer not in known or producer == consumer.instance_id:
                continue
            _add_edge(edges, producer, consumer.instance_id, EDGE.DATA_DEPENDENCY,
                      f"{consumer.instance_id} 使用 {producer} 的 {argument.select}",
                      consumer.source_id)
            constraints.append(Constraint(
                kind=ConstraintKind.DATA_DEPENDENCY, source_id=consumer.source_id,
                source_quote=consumer.source_quote,
                instances=[consumer.instance_id, producer],
                detail=dict(location=argument.location, name=argument.name,
                            select=argument.select, producer=producer),
                origin="POLICY_BINDING"))

    # 8. Cycle detection and stable order; a real contradiction is never repaired.
    order, cycle = topological_order(instances, edges)
    if cycle:
        ambiguities.append(Ambiguity(
            kind=AmbiguityKind.DEPENDENCY_CYCLE,
            message="依赖图存在真实循环，不能通过删除边或改序执行。",
            detail=dict(cycle=cycle, edges=[
                dict(target=target, after=source,
                     reasons=[reason.model_dump(mode="json")
                              for reason in edges.get((source, target), [])])
                for target in cycle for source in cycle if (source, target) in edges])))
        order = []

    # 9. Normalized requirements: only real requests remain Design obligations.
    normalized, applied, dropped = _normalize_requirements(requirements, clauses,
                                                           request_clauses)
    coverage = dict(
        source_clauses=len(clauses), request_clauses=len(request_clauses),
        assertion_clauses=sum(clause.role is ClauseRole.ASSERTION for clause in clauses),
        data_capture_clauses=sum(clause.role is ClauseRole.DATA_CAPTURE
                                 for clause in clauses),
        constraint_clauses=sum(clause.role is ClauseRole.CONSTRAINT for clause in clauses),
        unclassified_clauses=sum(clause.role is ClauseRole.UNCLASSIFIED
                                 for clause in clauses),
        required_clauses_before=len(raw_required), required_clauses_after=len(normalized),
        instances=len(instances), resources=len(resources),
        normalization_applied=applied, dropped_required=list(dropped),
        advisories=list(advisories))

    return PlanningIR(
        instances=instances, resources=resources, clauses=clauses,
        constraints=constraints,
        edges=[DependencyEdge(source_instance=source, target_instance=target, reasons=reasons)
               for (source, target), reasons in edges.items()],
        ambiguities=ambiguities, normalized_requirements=normalized,
        coverage=coverage, normalization_applied=applied, order=order)


def _clause_role(atom, requirement, seen_operations=(), input_quotes=(),
                 read_operations=()) -> ClauseRole:
    text = atom.get("text", "")
    read_actions = [action["operation_id"] for action in requirement.get("actions") or []
                    if action["operation_id"] in read_operations]
    if requirement.get("disposition") == "deferred" or atom.get("future_only"):
        return ClauseRole.DEFERRED
    if requirement.get("disposition") in {"constraint", "needs_review"}:
        return ClauseRole.CONSTRAINT
    if atom.get("constraint"):
        return ClauseRole.CONSTRAINT
    actions = requirement.get("actions") or []
    statuses = explicit_statuses(text)
    if text.strip() in input_quotes and not request_verbs(text):
        # "数量2" carries a confirmed input value for the request that names it; it is not a
        # second call and not an assertion.
        return ClauseRole.CONSTRAINT
    if len(actions) > 1:
        # A request can name a set of approved operations without spelling one HTTP verb per
        # operation ("测一下增删改查"). Each distinct named operation is one request instance;
        # resource identity and ordering still come from target-policy lifecycle evidence, not
        # from the order of this array. Repeating one operation in the same clause remains
        # ambiguous because the text does not say how many calls it means.
        operations = [action["operation_id"] for action in actions]
        if len(set(operations)) != len(operations):
            return ClauseRole.CONSTRAINT
        if (all(operation in seen_operations for operation in operations)
                and not request_verbs(text) and not statuses):
            # "看看增删改查有没有问题" restates a scope already requested without naming a
            # new action; it qualifies that scope rather than creating every call a second time.
            return ClauseRole.CONSTRAINT
        return ClauseRole.REQUEST
    if (actions and VERIFY_VERB.search(text) and not statuses
            and not _predicate_clause(text)):
        # A read-back after an update ("再确认") is an executable read even though it names no
        # read verb. A delete/update success confirmation is the write's reviewed status; it
        # must not manufacture a second POST/GET instance.
        if read_actions and (not WRITE_VERB.search(text) or READ_VERB.search(text)):
            return ClauseRole.REQUEST
        if all(action["operation_id"] in seen_operations for action in actions):
            return ClauseRole.CONSTRAINT
    if actions and request_verbs(text):
        return ClauseRole.REQUEST
    if verification_only(text) or (statuses and not request_verbs(text)):
        if statuses:
            return ClauseRole.ASSERTION
        # A selector/field predicate is a response rule the existing ledger already owns.
        if _predicate_clause(text):
            return ClauseRole.CONSTRAINT
        # "检查健康" asks to call that operation; "删除后确认成功" confirms a request already
        # asked for and therefore cannot create a second one. A bare confirmation without a
        # status or field predicate is not an independently executable assertion.
        if any(action["operation_id"] not in seen_operations for action in actions):
            return ClauseRole.REQUEST
        return ClauseRole.CONSTRAINT
    if data_capture(text) and not request_verbs(text):
        return ClauseRole.DATA_CAPTURE
    if not actions:
        return ClauseRole.CONSTRAINT
    if all(action["operation_id"] in seen_operations for action in actions):
        # No request verb, status, selector or confirmed input, and every named operation was
        # already requested: this is a qualifier or a meta instruction, not another call.
        return ClauseRole.CONSTRAINT
    return ClauseRole.UNCLASSIFIED


PREDICATE = re.compile(
    r"\$\.|\$\[|字段|属性|包含|等于|不等于|存在|非空|为空|不为空|状态码|表头|响应体|长度为")


def _predicate_clause(text: str) -> bool:
    return bool(PREDICATE.search(text or ""))


def _attach_assertion(instance, statuses, clause, constraints):
    instance.assertions.append(ResponseAssertion(
        statuses=list(statuses), source_id=clause.source_id,
        source_quote=clause.text))
    constraints.append(Constraint(
        kind=ConstraintKind.RESPONSE_ASSERTION, source_id=clause.source_id,
        source_quote=clause.text, instances=[instance.instance_id],
        detail=dict(statuses=list(statuses), anchor=instance.instance_id)))


def _assertion_anchor(clause, instances, by_source):
    """The instance a status-only clause confirms; the model's operation narrows the choice."""
    requirement = by_source.get(clause.source_id) or {}
    hints = {action["operation_id"] for action in requirement.get("actions") or []}
    preceding = [item for item in instances if item.source_start < clause.start]
    if not preceding:
        return None
    hinted = [item for item in preceding if item.operation_id in hints] if hints else []
    if hinted:
        return max(hinted, key=lambda item: item.sort_key)
    return max(preceding, key=lambda item: item.sort_key)


def _previous_request(clause, instances):
    preceding = [item for item in instances if item.source_start < clause.start]
    if not preceding:
        return None
    return max(preceding, key=lambda item: item.sort_key)


def _approved_absence_policy(policies, read_ops, deletion):
    """The approved 404/410 read policy that reads the same identity this deletion removes.

    The operator approval is the ordinary Policy schema: a reviewed status assertion on a read
    operation, bound through the same producer links as the deletion. The link is the *identity*
    (both bind the path parameter from the create), not the deletion policy id, so an injected
    ``<read>_absence404`` copy and a natively reviewed rule are both recognized.
    """
    producers = set()
    for candidate in policies:
        if candidate["operation_id"] != deletion.operation_id:
            continue
        for rule in candidate.get("binding_rules") or []:
            if rule.get("source_policy_id"):
                producers.add(rule["source_policy_id"])
    if not producers:
        return None
    for policy in policies:
        if policy.get("negative_test"):
            continue
        if policy["operation_id"] not in read_ops:
            continue
        codes = set(_status_codes(policy))
        if not codes or not codes <= {404, 410}:
            continue
        if deletion.role not in (policy.get("roles") or []):
            continue
        sources = {rule.get("source_policy_id") for rule in policy.get("binding_rules") or []}
        if sources & producers:
            return policy
    return None


def _consumes_resource(instance, producer, operations, rule):
    """A call on the created resource's path, not a same-named operation."""
    if instance.operation_id == rule["delete_operation"]:
        return True
    operation = operations.get(instance.operation_id)
    create = operations.get(producer.operation_id)
    if not operation or not create:
        return False
    return (operation.get("method") in {"GET", "HEAD", "PATCH", "PUT", "DELETE"}
            and operation.get("path") ==
            create.get("path", "").rstrip("/") + "/{" + rule["path_parameter"] + "}")


def _phase_for(instance):
    statuses = {status for assertion in instance.assertions
                for status in assertion.statuses}
    return (ResourcePhase.VERIFY_ABSENT.value if 404 in statuses
            else ResourcePhase.EXISTS.value)


def _non_request_order_side(order, side, clauses):
    """Whether one unresolved order side names a clause that is not an executable request."""
    source = order.get(f"{side}_source_id")
    if source is None:
        return False
    clause = next((item for item in clauses if item.source_id == source), None)
    return clause is not None and clause.role in {ClauseRole.ASSERTION, ClauseRole.CONSTRAINT}


def _order_side(order, side, instances, clauses):
    """Resolve one side of a written order to the instances it names, or ``None``.

    Instances the IR *derived* (a post-delete absence readback) are not clause actions, so a
    text order such as "create before read" must not become ambiguous merely because the read
    operation now occurs twice.
    """
    source = order.get(f"{side}_source_id")
    index = order.get(f"{side}_index")
    operation = order.get(side)

    def named(item):
        return item.origin != ABSENCE_INSTANCE_ORIGIN

    def one_of(items):
        """Prefer the clause's own action; fall back to a lifecycle verification if it is the
        only instance of that operation (the request itself asked for the post-delete read)."""
        preferred = [item for item in items if named(item)]
        pool = preferred or list(items)
        return pool if len(pool) == 1 else None

    if source is None:
        return one_of([item for item in instances if item.operation_id == operation])
    matched = [item for item in instances
               if item.source_id == source and item.action_index == (index or 0)]
    if matched:
        return one_of(matched) or matched
    if index is None:
        # A side that names only an operation inside a known request clause: the operation is
        # the unambiguous witness as long as it occurs once among the clause's own actions.
        by_operation = one_of([item for item in instances if item.source_id == source
                               and item.operation_id == operation])
        if by_operation is not None:
            return by_operation
    # A removed assertion clause names no request; its anchor is the request it qualifies.
    clause = next((item for item in clauses if item.source_id == source), None)
    if clause is not None and clause.role is ClauseRole.REQUEST:
        return [item for item in instances if item.source_id == source and named(item)]
    return None


def _select_policy(instance, policies, ambiguities):
    candidates = _policy_candidates(policies, instance.operation_id, instance.role)
    if not candidates:
        ambiguities.append(Ambiguity(
            kind=AmbiguityKind.OPERATION_NOT_APPROVED, source_id=instance.source_id,
            message=f"接口 {instance.operation_id} 没有已批准的身份策略。"))
        return
    asserted = [status for assertion in instance.assertions
                for status in assertion.statuses]
    chosen = None
    if asserted:
        exact = [policy for policy in candidates
                 if set(_status_codes(policy)) == set(asserted)]
        contains = [policy for policy in candidates
                    if set(asserted) <= set(_status_codes(policy))]
        if len(exact) == 1:
            chosen = exact[0]
        elif not exact and len(contains) == 1:
            chosen = contains[0]
        elif not exact and not contains:
            ambiguities.append(Ambiguity(
                kind=AmbiguityKind.RESPONSE_STATUS_NOT_APPROVED,
                source_id=instance.source_id,
                message=f"原文要求的状态 {asserted} 没有对应的已批准策略。",
                detail=dict(operation_id=instance.operation_id, statuses=asserted)))
    else:
        plain = [policy for policy in candidates if not _is_expectation(policy)]
        if len(plain) == 1:
            chosen = plain[0]
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            ambiguities.append(Ambiguity(
                kind=AmbiguityKind.MISSING_INSTANCE_IDENTITY,
                source_id=instance.source_id,
                message="没有写明期望状态，且存在多个已批准策略，无法确定断言。",
                detail=dict(operation_id=instance.operation_id,
                            policies=[policy["id"] for policy in candidates])))
    if chosen is None:
        return
    instance.policy_id = chosen["id"]
    instance.policy_origin = "ASSERTED_STATUS" if asserted else "OPERATION_POLICY"


def _resolve_binding_arguments(instance, policy, policy_instances, input_by_slot,
                               ambiguities):
    """Turn one approved binding rule into an approved literal or a producer reference."""
    for rule in policy.get("binding_rules") or []:
        location, name = rule.get("location"), rule.get("name")
        if location not in {"body", "path_parameters", "query", "headers"} or not name:
            continue
        producer_policy = rule.get("source_policy_id")
        producers = policy_instances.get(producer_policy, []) if producer_policy else []
        bound = None
        if producer_policy:
            if len(producers) == 1:
                bound = producers[0]
            else:
                ambiguities.append(Ambiguity(
                    kind=AmbiguityKind.PARAMETER_SOURCE_UNKNOWN,
                    source_id=instance.source_id,
                    message="参数来源策略没有唯一的产生者实例。",
                    detail=dict(policy_id=producer_policy, producers=producers)))
                continue
        value = input_by_slot.get((instance.operation_id, location, name))
        if value is None and rule.get("literal_schema"):
            choices = (rule["literal_schema"] or {}).get("enum") or []
            if len(choices) == 1:
                value = choices[0]
        if bound is not None:
            argument = InstanceArgument(location=location, name=name,
                                        from_instance=bound, select=rule.get(
                                            "source_selector", "$"))
        elif value is not None or rule.get("literal_schema") is not None:
            argument = InstanceArgument(location=location, name=name, value=value,
                                        origin="APPROVED_LITERAL")
        else:
            continue
        instance.arguments = [item for item in instance.arguments
                              if (item.location, item.name) != (location, name)] + [argument]


def _normalize_requirements(requirements, clauses, request_clauses):
    """Drop constraint clauses from Design obligations; keep only executable requests."""
    clause_by_source = {clause.source_id: clause for clause in clauses}
    normalized = []
    dropped = []
    for requirement in requirements:
        if requirement.get("disposition") != "required":
            continue
        if requirement["source_id"] not in request_clauses:
            # A required clause the IR could not treat as an executable request. An assertion,
            # data-capture or constraint clause is *expected* here (it is not a Design
            # obligation); an unclassified or deferred one means an action would be lost, so the
            # legacy path must keep it and this is recorded rather than silently deleted.
            clause = clause_by_source.get(requirement["source_id"])
            role = getattr(getattr(clause, "role", None), "value", None)
            if role in {None, "DEFERRED", "UNCLASSIFIED"}:
                dropped.append(requirement["source_id"])
            continue
        item = {key: requirement.get(key) for key in (
            "source_id", "source_quote", "disposition", "actions", "before",
            "same_resource", "existing_resources", "response_checks",
            "forbidden_operation_ids", "blocked_operation_ids")}
        for key in ("actions", "before", "same_resource", "existing_resources",
                    "response_checks", "forbidden_operation_ids", "blocked_operation_ids"):
            item[key] = item.get(key) or []
        item["before"] = []
        for order in requirement.get("before") or []:
            remapped = _remap_order(order, clause_by_source, request_clauses)
            if remapped is not None:
                item["before"].append(remapped)
        item["same_resource"] = [pair for pair in requirement.get("same_resource") or []
                                 if pair.get("producer") in request_clauses
                                 or pair.get("consumer") in request_clauses]
        from agenttest.request_requirements import RequestRequirement

        normalized.append(RequestRequirement.model_validate(item).model_dump())
    applied = len(normalized) != len([item for item in requirements
                                      if item.get("disposition") == "required"])
    return normalized, applied, dropped


def _remap_order(order, clause_by_source, request_clauses):
    """Keep an order only when both sides name executable request clauses."""
    result = dict(order)
    for side in ("earlier", "later"):
        source = result.get(f"{side}_source_id")
        if source is None:
            continue
        if source in request_clauses:
            continue
        clause = clause_by_source.get(source)
        if clause is None or clause.role is not ClauseRole.ASSERTION:
            return None
        return None
    return result
