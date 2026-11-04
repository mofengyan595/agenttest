"""Independent, bounded extraction of explicit assignments; never grants test authority."""

import json
import re

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.llm_planner import literal_data

_ASSIGNMENT = re.compile(
    r'(?<![A-Za-z0-9_.])(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[=＝]\s*'
    r'(?P<value>"(?:[^"\\]|\\.)*"|“[^”]*”|\'[^\']*\'|'
    r'-?\d+(?:\.\d+)?|true|false|null)(?![A-Za-z0-9_]|\.\d)')


def explicit_assignments(text):
    """Recognize only field=scalar syntax, retaining offsets and deferred evidence.

    This is not a general semantic parser. Ambiguous fields and unknown business
    expectations remain reviewable, rather than becoming assertions or permission.
    """
    result = []
    for match in _ASSIGNMENT.finditer(text):
        raw = match['value']
        if raw.startswith(('“', "'")):
            value = raw[1:-1]
        elif raw[0] in '-0123456789':
            value = float(raw) if '.' in raw else int(raw)
        else:
            value = json.loads(raw)
        start = max(text.rfind(c, 0, match.start()) for c in '。；;，,\n') + 1
        ends = [pos for c in '。；;，,\n' if (pos := text.find(c, match.end())) >= 0]
        clause = text[start:min(ends) if ends else len(text)]
        prefix = text[start:match.start()]
        result.append(dict(name=match['name'], value=value, source_quote=match.group(),
            start=match.start(), end=match.end(),
            deferred=bool(re.search(r'(?:留给)?(?:以后|下次|将来|未来)(?:再|检查|测试)', prefix))
                and not any(cue in clause for cue in ('现在', '本轮', '本次'))))
    return result


def reconcile_assignments(context, selection):
    """Audit raw text against selected inputs/checks without relying on selected items alone."""
    checks = {c['policy_id']: c for c in context.get('approved_checks', [])}
    fields = context.get('input_fields', [])
    ledger = []
    for item in explicit_assignments(context['user_task']):
        candidates = {f['operation_id'] for f in fields if f['name'] == item['name']}
        candidates.update(c['operation_id'] for c in checks.values() if c['name'] == item['name'])
        responses = context.get('response_expectations', {})
        candidates.update(c['operation_id'] for c in responses.values()
                          if c.get('select', '').rsplit('.', 1)[-1] == item['name'])
        entry = dict(**item, candidate_operations=sorted(candidates), status='UNACCOUNTED')
        if item['deferred']:
            entry['status'] = 'DEFERRED'
        elif not candidates:
            entry['status'] = 'UNKNOWN_FIELD'
        else:
            matched = [r.operation_id for r in selection.input_requirements
                if r.name == item['name'] and not r.path
                and json_equal(literal_data(r.value), item['value'])
                and item['source_quote'] in r.source_quote]
            for requested in selection.requested_checks:
                check = checks.get(requested.policy_id, {})
                value = (literal_data(requested.exact_value.value)
                         if requested.exact_value is not None else check.get('invalid_value'))
                if (check.get('name') == item['name'] and json_equal(value, item['value'])
                        and item['source_quote'] in requested.source_quote):
                    matched.append(check['operation_id'])
            for requirement in selection.requirements:
                if item['source_quote'] not in requirement.source_quote:
                    continue
                for ref in requirement.response_checks:
                    rule = context.get('response_expectations', {}).get(ref, {})
                    if (rule.get('operator') == 'equals'
                            and rule.get('select', '').rsplit('.', 1)[-1] == item['name']
                            and json_equal(rule.get('expected'), item['value'])
                            and re.search(r'响应|返回|核对', requirement.source_quote)):
                        matched.append(rule['operation_id'])
            if matched:
                entry.update(status='ACCOUNTED', matched_operations=sorted(set(matched)))
            elif any(q.operation_id in candidates and item['source_quote'] in q.source_quote
                     for q in [*selection.input_questions, *selection.expectation_questions]):
                entry['status'] = 'NEEDS_REVIEW'
        ledger.append(entry)
    return ledger


def validate_assignment_coverage(context, selection):
    ledger = reconcile_assignments(context, selection)
    if any(item['status'] in {'UNACCOUNTED', 'UNKNOWN_FIELD'} for item in ledger):
        error = ValidationFailure(Layer.MODEL, 'FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED')
        error.requirement_ledger = ledger
        raise error
    return ledger
