"""Construct one ordered plan from unordered proposals before all execution validation."""

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import GenericPlannerDraft

ENCODING = 'UNORDERED_APPROVED_NODES_V1'


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
                edges[node['id']].add(existing[0]['id'])
    for index, node in enumerate(nodes.values()):
        for argument in node['arguments']:
            source = argument.get('from_step')
            if source is not None:
                if source not in nodes or source == node['id']:
                    raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REFERENCE_INVALID')
                edges[node['id']].add(source)
        for prerequisite in policies[node['policy_id']].get('prerequisite_policy_ids', []):
            producers = [n['id'] for n in nodes.values() if n['policy_id'] == prerequisite]
            if not producers:
                error = ValidationFailure(Layer.MODEL, 'CONSTRUCTION_PREREQUISITE_AMBIGUOUS')
                error.diagnostic.fields = [f'plan.steps.{index}.policy_id']
                raise error
            edges[node['id']].update(producers)
    for requirement in context.get('request_requirements', []):
        if requirement['disposition'] != 'required':
            continue
        for order in requirement['before']:
            later = [n['id'] for n in nodes.values() if n['operation_id'] == order['later']]
            if not later:
                continue
            earlier = [n['id'] for n in nodes.values() if n['operation_id'] == order['earlier']]
            if not earlier:
                raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS')
            for target in later:
                edges[target].update(earlier)
    for earlier, later in zip(context.get('composed_negative_chain', []),
                              context.get('composed_negative_chain', [])[1:]):
        if earlier not in nodes or later not in nodes:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_COMPOSED_CHAIN_INVALID')
        edges[later].add(earlier)

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
                            edges[deletion['id']].add(consumer['id'])
    ordered, pending = [], set(nodes)
    while pending:
        available = sorted(key for key in pending if not edges[key] & pending)
        if not available:
            raise ValidationFailure(Layer.MODEL, 'CONSTRUCTION_DEPENDENCY_CYCLE')
        for key in available:
            ordered.append(nodes[key])
            pending.remove(key)
    return dict(name=draft.name, steps=ordered), dict(encoding=ENCODING,
        proposed_nodes=len(draft.steps), constructed_nodes=len(ordered),
        managed_authentication_nodes=len(inserted), dependency_edges=sum(map(len, edges.values())),
        node_dependencies={key: sorted(value) for key, value in edges.items()})


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
    for proposed_index, case in enumerate(joined):
        plan, record = construct_plan(case['plan'], {**context,
            'composed_negative_chain': chains.get(proposed_index, [])})
        edges = {key: set(value) for key, value in record.pop('node_dependencies').items()}
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
