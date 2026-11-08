"""Read-only cold verifier. Does not run a model, target, or overwrite any original score."""
import hashlib
import json
import sys
from pathlib import Path

folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
frozen = json.loads((folder / 'freeze.json').read_bytes())
assert hashlib.sha256((folder / 'freeze.json').read_bytes()).hexdigest() == (
    folder / 'freeze.sha256').read_text().strip()
evaluator = folder / 'evaluator'
assert all(hashlib.sha256((evaluator / name).read_bytes()).hexdigest() == digest
           for name, digest in frozen['evaluator_sha256'].items())
sys.path.insert(0, str(evaluator))
from collect import score_position

results = json.loads((folder / 'results.json').read_bytes())
counter = 0
for arm in ('baseline', 'candidate'):
    positions = results['results'][arm]['positions']
    for position in positions:
        score = score_position(folder, position['id'])
        assert score == json.loads((folder / position['id'] / 'score.json').read_bytes())
        assert all(score[key] == position[key] for key in score)
        terminal = json.loads((folder / position['id'] / 'terminal.json').read_bytes())
        usage = terminal['summary']['context_usage']
        assert usage['known_tokens'] == position['known_tokens']
        assert usage['model_calls'] == position['model_calls']
        counter += 1
    for key in ('full_execution', 'correct_hold', 'false_refusal', 'silent_obligations',
                'configured_scope_violations', 'task_scope_violations', 'pending_scope_violations',
                'model_calls', 'known_tokens'):
        assert sum(p[key] for p in positions) == results['results'][arm]['totals'][key]
assert counter == 48
print(json.dumps(dict(positions_verified=counter, scorer_frozen=True, original_scores_match=True,
                     aggregates_match=True, new_model_calls=0, new_target_requests=0)))
