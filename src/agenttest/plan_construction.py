"""Construct one ordered plan from unordered proposals before all execution validation."""

import json

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import GenericPlannerDraft

ENCODING = 'UNORDERED_APPROVED_NODES_V1'


def _same_request(left, right):
    """Two proposals that would send exactly the same request (a replay group member)."""
    fields = ('operation_id', 'role', 'policy_id')
    return (all(left.get(field) == right.get(field) for field in fields)
            and json.dumps(left.get('arguments') or [], sort_keys=True, ensure_ascii=False,
                           default=str)
            == json.dumps(right.get('arguments') or [], sort_keys=True, ensure_ascii=False,
                          default=str))


def construct_plan(plan, context):
    """Use trusted dependencies, without retries, rejected-plan edits or business defaults.

    Array position in this encoding has no execution meaning. Only authentication
    nodes may be introduced; all business nodes and literal inputs remain unchanged.
    Missing or ambiguous business predecessors are a review stop, never auto-created.
    """
    draft = GenericPlannerDraft.model_validate(plan)
    nodes = {}
    for node in draft.steps:
        if node.id in nodes:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_DUPLICATE_NODE')
        nodes[node.id] = node.model_dump(mode='json')
    policies = {p['id']: p for p in context.get('policies', [])}
    if any(n['policy_id'] not in policies or policies[n['policy_id']]['operation_id'] !=
           n['operation_id'] or n['role'] not in policies[n['policy_id']]['roles']
           for n in nodes.values()):
        raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_NODE_NOT_APPROVED')
    edges = {key: set() for key in nodes}
    reasons = {}

    def depend(target, source, reason):
        """Every edge records the approved rule or the model reference that produced it."""
        if source == target:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REFERENCE_INVALID')
        edges[target].add(source)
        reasons[(target, source)] = reason

    inserted = []
    for auth in context.get('authentication', []):
        protected = [n for n in list(nodes.values()) if n['role'] == auth['role']
                     and n['operation_id'] in auth['before_operation_ids']]
        if not protected or auth['login_operation_id'] is None:
            continue
        existing = [n for n in nodes.values() if n['operation_id'] ==
                    auth['login_operation_id'] and n['role'] == auth['role']]
        if not existing:
            allowed = [p for p in policies.values() if p['operation_id'] ==
                       auth['login_operation_id'] and auth['role'] in p['roles']
                       and not p.get('negative_test')]
            if len(allowed) != 1:
                raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_AUTHENTICATION_AMBIGUOUS')
            key = f"managed_auth_{auth['role']}"
            if key in nodes:
                raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_RESERVED_NODE_ID')
            node = dict(id=key, operation_id=auth['login_operation_id'], role=auth['role'],
                        policy_id=allowed[0]['id'], arguments=[])
            nodes[key], edges[key] = node, set()
            inserted.append(key)
            existing = [node]
        if len(existing) != 1:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_AUTHENTICATION_AMBIGUOUS')
        for node in protected:
            if node['id'] != existing[0]['id']:
                depend(node['id'], existing[0]['id'], 'AUTHENTICATION')
    for index, node in enumerate(nodes.values()):
        for argument in node['arguments']:
            source = argument.get('from_step')
            if source is not None:
                if source not in nodes:
                    raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REFERENCE_INVALID')
                depend(node['id'], source, 'MODEL_REFERENCE')
        for prerequisite in policies[node['policy_id']].get('prerequisite_policy_ids', []):
            producers = [n['id'] for n in nodes.values() if n['policy_id'] == prerequisite]
            if not producers:
                error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_PREREQUISITE_AMBIGUOUS')
                error.diagnostic.fields = [f'plan.steps.{index}.policy_id']
                raise error
            for producer in producers:
                depend(node['id'], producer, 'POLICY_PREREQUISITE')
    ordered_positions = {key: position for position, key in enumerate(nodes)}

    def bound_instances():
        """Map each (clause, action occurrence) onto the nodes that realize it.

        One instance owns one node. Byte-identical nodes on one instance are a replay group:
        their internal order carries no user intent and is serialised on its own.
        """
        found = {}
        for key, node in nodes.items():
            source = node.get('requirement_source_id')
            if source is None:
                continue
            instance = (source, node.get('requirement_action_index') or 0)
            group = found.setdefault(instance, [])
            if group and any(not _same_request(nodes[key], nodes[other]) for other in group):
                error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_ORDER_INSTANCE_CONFLICT')
                error.diagnostic.fields = [f'plan.steps.{ordered_positions[key]}.'
                                           f'requirement_source_id']
                raise error
            group.append(key)
        return found

    bound = bound_instances()

    def node_for(instances, operation, order, *, identity_required=False):
        """The concrete nodes for one order side; never guesses from proposal order."""
        resolved = [instance for instance in instances if instance[0] is not None]
        if resolved:
            group, unbound = [], []
            for instance in resolved:
                found = bound.get(instance)
                if not found:
                    unbound.append(instance)
                    continue
                if any(nodes[key]['operation_id'] != operation for key in found):
                    error = ValidationFailure(Layer.MODEL,
                                              'CONSTRUCTION_ORDER_INSTANCE_INVALID')
                    error.diagnostic.fields = [f'plan.steps.{ordered_positions[key]}.operation_id'
                                               for key in found]
                    raise error
                group.extend(found)
            if not unbound:
                return group
            pool = [key for key, node in nodes.items() if node['operation_id'] == operation]
            replay = (len(resolved) == 1 and len(pool) > 1
                      and all(_same_request(nodes[pool[0]], nodes[key]) for key in pool[1:]))
            if identity_required and not replay:
                error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_ORDER_INSTANCE_UNBOUND')
                error.diagnostic.fields = [f'requirement_map.{instance[0]}.'
                                           f'requirement_action_index' for instance in unbound]
                raise error
            # Different operations need no identity: every earlier call precedes every later
            # one, so the whole operation realizes the side without picking a slot.
            if pool:
                return pool
            error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
            error.construction = dict(order=order, candidates=[], edge_reason='TEXT_ORDER')
            raise error
        candidates = [key for key, node in nodes.items() if node['operation_id'] == operation]
        if len(candidates) == 1 or candidates and all(
                _same_request(nodes[candidates[0]], nodes[key]) for key in candidates[1:]):
            return candidates
        error = ValidationFailure(Layer.MODEL,
                                  'CONSTRUCTION_ORDER_INSTANCE_AMBIGUOUS' if candidates
                                  else 'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
        error.diagnostic.fields = [f'plan.steps.{ordered_positions[key]}' for key in candidates]
        error.construction = dict(order=order, candidates=candidates,
                                  edge_reason='TEXT_ORDER')
        raise error

    from agenttest.request_requirements import order_instances

    for requirement in context.get('request_requirements', []):
        if requirement['disposition'] != 'required':
            continue
        for order in requirement['before']:
            earlier_instances, later_instances = order_instances(
                order, context.get('request_requirements', []),
                {atom['source_id']: atom for atom in context.get('request_atoms', [])})
            if not any(node['operation_id'] == order['later'] for node in nodes.values()):
                if order['later'] in context.get('design_operations', ()):
                    # The required order spans execution contexts; the design-level witness
                    # checks the bound instances before any HTTP.
                    continue
                raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
            if not any(node['operation_id'] == order['earlier'] for node in nodes.values()):
                if order['earlier'] in context.get('design_operations', ()):
                    # The first half of the pair executes in an earlier case.
                    continue
                raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
            earlier = node_for(earlier_instances, order['earlier'], order,
                               identity_required=order['earlier'] == order['later'])
            target = node_for(later_instances, order['later'], order,
                              identity_required=order['earlier'] == order['later'])
            if set(earlier) & set(target):
                # One node cannot follow itself. A replay group may serve both sides; its
                # members are then serialised as a replay, never as user intent.
                if earlier_instances != later_instances or len(earlier) < 2:
                    error = ValidationFailure(Layer.MODEL,
                                              'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
                    error.diagnostic.fields = [f'requirement_map.{requirement["source_id"]}'
                                               f'.before']
                    error.construction = dict(order=order, candidates=sorted(set(earlier)),
                                              edge_reason='TEXT_ORDER')
                    raise error
                for previous, current in zip(earlier, earlier[1:]):
                    depend(current, previous, 'REPLAY_GROUP')
                continue
            for current in target:
                for previous in earlier:
                    depend(current, previous, 'TEXT_ORDER')
    for earlier, later in zip(context.get('composed_negative_chain', []),
                              context.get('composed_negative_chain', [])[1:]):
        if earlier not in nodes or later not in nodes:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_COMPOSED_CHAIN_INVALID')
        depend(later, earlier, 'COMPOSED_CHAIN')

    def ancestors(key, seen=None):
        seen = set() if seen is None else seen
        for predecessor in edges[key] - seen:
            seen.add(predecessor)
            ancestors(predecessor, seen)
        return seen

    # Operator-approved lifecycle cleanup follows consumers of that exact fresh ID.
    # Explicit post-delete checks already depend on deletion and stay after it.
    for lifecycle in context.get('resource_lifecycles', []):
        for deletion in nodes.values():
            if deletion['operation_id'] != lifecycle['delete_operation']:
                continue
            sources = [a['from_step'] for a in deletion['arguments'] if
                a['location'] == 'path_parameters' and a['name'] == lifecycle['path_parameter']
                and a.get('select') == lifecycle['id_selector'] and a.get('from_step') in nodes
                and nodes[a['from_step']]['operation_id'] == lifecycle['create_operation']]
            for source in sources:
                for consumer in nodes.values():
                    if consumer['id'] != deletion['id'] and any(
                            a.get('from_step') == source for a in consumer['arguments']):
                        if deletion['id'] not in ancestors(consumer['id']):
                            depend(deletion['id'], consumer['id'], 'LIFECYCLE_CLEANUP')

    def cycle_path(pending):
        """One concrete cycle inside the remaining nodes, as an ordered node list."""
        start = sorted(pending)[0]
        trail = []

        def walk(key, seen):
            trail.append(key)
            for predecessor in sorted(edges[key] & pending):
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

    ordered, pending = [], set(nodes)
    while pending:
        available = sorted(key for key in pending if not edges[key] & pending)
        if not available:
            # A cycle between approved rules is a real conflict, never repaired by deleting an
            # edge: report the concrete path and the rule behind every edge so a human can
            # decide, and stop before any request.
            path = cycle_path(pending)
            error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_DEPENDENCY_CYCLE')
            error.diagnostic.fields = [f'plan.steps.{ordered_positions[key]}'
                                       for key in path if key in ordered_positions]
            error.construction = dict(cycle=list(path), edges=[
                dict(step=target, after=source, reason=reasons.get((target, source)))
                for target in path for source in sorted(edges[target])
                if source in path])
            raise error
        for key in available:
            ordered.append(nodes[key])
            pending.remove(key)
    return dict(name=draft.name, steps=ordered), dict(encoding=ENCODING,
        proposed_nodes=len(draft.steps), constructed_nodes=len(ordered),
        managed_authentication_nodes=len(inserted), dependency_edges=sum(map(len, edges.values())),
        node_dependencies={key: sorted(value) for key, value in edges.items()},
        # Per-edge provenance: a data reference and a written order are different evidence for
        # the same pair, so a caller can prove which one produced an edge.
        edge_reasons={f"{target}<={source}": reason
                      for (target, source), reason in reasons.items()})


def join_read_prerequisite(cases, context):
    """Join a unique approved predecessor pair before assigning execution contexts."""
    if len(cases) != 2 or not all(isinstance(case, dict) and case.get('kind') in {
            'normal', 'flow', 'permission'} and isinstance(case.get('plan'), dict)
            and isinstance(case['plan'].get('steps'), list) and len(case['plan']['steps']) == 1
            for case in cases):
        return cases
    from agenttest.product_design import DesignCase

    steps = [DesignCase.model_validate(case).plan.steps[0] for case in cases]
    methods = {op['id']: op['method'] for op in context.get('operations', [])}
    policies = {p['id']: p for p in context.get('policies', [])}
    if steps[0].role != steps[1].role or any(
            methods.get(step.operation_id) not in {'GET', 'HEAD'}
            or step.policy_id not in policies or policies[step.policy_id].get('negative_test')
            or any(arg.from_step is not None for arg in step.arguments) for step in steps):
        return cases
    for consumer, producer in ((0, 1), (1, 0)):
        if steps[producer].policy_id not in policies[steps[consumer].policy_id].get(
                'prerequisite_policy_ids', []):
            continue
        primary = cases[consumer]
        predecessor = steps[producer].model_dump(mode='json')
        if predecessor['id'] == steps[consumer].id:
            predecessor['id'] = ('linked_predecessor' if steps[consumer].id != 'linked_predecessor'
                                 else 'linked_prerequisite')
        return [{**primary, 'plan': {**primary['plan'], 'steps': [predecessor,
                                                                  *primary['plan']['steps']]}}]
    return cases


def construct_design(wire, context, *, construction_chains=()):
    records, cases = [], []
    chains = dict(construction_chains)
    proposed = wire.get('cases', [])
    joined = join_read_prerequisite(proposed, context) if not chains else proposed
    # A required order may name an operation this case does not contain but the proposed
    # design schedules in another execution context.
    design_operations = {step.get('operation_id') for case in joined
                         if isinstance(case, dict) and isinstance(case.get('plan'), dict)
                         for step in case['plan'].get('steps', []) if isinstance(step, dict)}
    for proposed_index, case in enumerate(joined):
        plan, record = construct_plan(case['plan'], {**context,
            'design_operations': design_operations,
            'composed_negative_chain': chains.get(proposed_index, [])})
        edges = {key: set(value) for key, value in record.pop('node_dependencies').items()}
        record.pop('edge_reasons', None)
        nodes = {node['id']: node for node in plan['steps']}
        authentication = {node['id'] for node in plan['steps'] if any(
            node['operation_id'] == auth['login_operation_id'] and node['role'] == auth['role']
            for auth in context.get('authentication', [])) and any(
                node['id'] in dependencies for dependencies in edges.values())}
        pending, components = set(nodes) - authentication, []
        while pending:
            component, frontier = set(), {min(pending)}
            while frontier:
                current = frontier.pop()
                component.add(current)
                neighbors = edges[current] | {key for key in pending if current in edges[key]}
                frontier.update((neighbors & pending) - component - authentication)
            pending -= component
            components.append(component)
        if not components:
            components = [set(nodes)]
        for index, component in enumerate(components):
            included = set(component)
            while True:
                dependencies = set().union(*(edges[key] for key in included))
                if dependencies <= included:
                    break
                included |= dependencies
            current = {**case, 'plan': dict(name=plan['name'], steps=[node
                for node in plan['steps'] if node['id'] in included])}
            if len(components) > 1:
                current['title'] = case['title'][:85] + f'（独立检查{index + 1}）'
                if current['kind'] == 'negative' and not any(
                        next(p for p in context['policies'] if p['id'] == node['policy_id']).get(
                            'negative_test') for node in current['plan']['steps']):
                    current['kind'] = 'normal'
            cases.append(current)
        record['independent_contexts'] = len(components)
        if joined is not proposed:
            record['joined_read_cases'] = len(proposed)
        records.append(record)
    wire['cases'] = cases
    return records
