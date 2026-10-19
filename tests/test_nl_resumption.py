"""No task outputs or paid calls: validate exact scheduling prefix semantics."""

import copy

import pytest

pytest.importorskip("langgraph")

from agenttest import nl_resumption as resumption


def ledger():
    schedule = [dict(case_id=f"fixture_{i}", arm="candidate" if i % 2 else "baseline",
                     category="healthy", round=1) for i in range(72)]
    first = dict(**schedule[0], run_id="a" * 32, stop_paid=True,
                 usage_known=False, total_tokens=None)
    rows = [first] + [dict(**s, run_id=None, not_attempted_reason="STOP")
                      for s in schedule[1:]]
    return dict(source="real", results=rows), dict(schedule=schedule)


def test_unknown_original_position_is_retained_and_only_exact_suffix_selected():
    prior, frozen = ledger()
    saved = copy.deepcopy(prior)
    prefix, suffix = resumption.suffix_for(prior, frozen)
    assert prefix == prior["results"][:1]
    assert suffix == frozen["schedule"][1:]
    assert len(suffix) == 71 and prefix[0]["total_tokens"] is None
    assert prior == saved


@pytest.mark.parametrize("mutation", ["gap", "unknown_attempt", "slot_changed", "no_stop"])
def test_started_or_ambiguous_positions_cannot_be_selected_again(mutation):
    prior, frozen = ledger()
    if mutation == "gap":
        prior["results"][2]["run_id"] = "b" * 32
    elif mutation == "unknown_attempt":
        prior["results"][1]["attempted_unknown"] = True
    elif mutation == "slot_changed":
        prior["results"][1]["case_id"] = "different_slot"
    else:
        prior["results"][0]["stop_paid"] = False
    with pytest.raises(ValueError):
        resumption.suffix_for(prior, frozen)


def test_usage_reconciliation_is_not_inferred_from_permission_to_continue():
    prior, _ = ledger()
    prior["results"][0]["model_calls"] = 1
    prior["results"][1].update(run_id="b" * 32, usage_known=True,
                              total_tokens=123, model_calls=1)
    actual = resumption.usage_audit(prior["results"])
    assert actual == dict(known_token_subtotal=123, unknown_usage_positions=[1],
                         total_tokens=None, reported_model_calls_subtotal=2)
