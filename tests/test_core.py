import datetime as dt

import pytest
import yaml

from mpt import CONFIG_DIR, agents, aggregate, probe, snapshot
from mpt.llm import Budget, BudgetExceeded, Cache, Client, parse_json, placeholder


def test_release_dates():
    monthly = snapshot.Release(lag_months=2, release_day=6)
    assert snapshot.release_date("2026-07", monthly) == dt.date(2026, 9, 6)
    assert snapshot.release_date("2026-11", monthly) == dt.date(2027, 1, 6)
    assert snapshot.release_date("2026-Q3", snapshot.Release(release_day=28)) == dt.date(2026, 7, 28)
    assert snapshot.release_date("2026-09-14", snapshot.Release(lag_days=1)) == dt.date(2026, 9, 15)


def test_information_set_excludes_unpublished_data():
    raw = {"fetched_at": "x", "series": {
        "dfr": {"description": "", "source": "", "rule": {"lag_months": 0, "release_day": 1, "lag_days": 0},
                "obs": [("2026-06-16", 2.0), ("2026-06-17", 2.25), ("2026-09-16", 2.5)]},
        "mir": {"description": "", "source": "", "rule": {"lag_months": 2, "release_day": 6, "lag_days": 0},
                "obs": [("2026-07", 3.8), ("2026-08", 3.9)]},
    }}
    info = snapshot.information_set(raw, dt.date(2026, 9, 15), [])
    assert info["series"]["dfr"]["obs"] == [("2026-06-16", 2.0), ("2026-06-17", 2.25)]
    assert info["series"]["mir"]["obs"] == [("2026-07", 3.8)]


def test_net_percentage():
    codes = {"a": [1, 2, 3, 3], "b": [4, 3]}  # a: 50% tighten; b: 50% ease
    assert aggregate.net_percentage(codes, {"a": 0.75, "b": 0.25}) == pytest.approx(25.0)


def test_weighted_median():
    assert aggregate.weighted_median([1, 2, 10], [0.2, 0.2, 0.6]) == 10
    assert aggregate.weighted_median([1, 2, 10], [1, 1, 1]) == 2


def test_probe_score():
    assert probe.score({"known": True, "value": 2.5}, 2.5)
    assert not probe.score({"known": False, "value": 2.5}, 2.5)
    assert not probe.score({"known": True, "value": None}, 2.5)


def test_archetype_weights_and_personas():
    a = yaml.safe_load((CONFIG_DIR / "archetypes.yaml").read_text())
    assert len(agents.banks(a)) == 9
    hh = agents.households(a, ["DE", "ES"])
    assert len(hh) == 30
    assert sum(h.weight for h in hh if h.country == "ES") == pytest.approx(1.0)
    assert all((h.mortgage_type is not None) == (h.tenure == "mortgagor") for h in hh)


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_placeholder_matches_schema():
    answer = placeholder(agents.bank_schema())
    assert set(answer) == set(agents.bank_schema()["required"])
    assert answer["enterprise_past_3m"] in agents.STANDARDS_SCALE


def test_budget_blocks_overspend():
    b = Budget(1.0)
    b.reserve(0.6)
    with pytest.raises(BudgetExceeded):
        b.reserve(0.6)
    b.settle(0.6, 0.1)
    b.reserve(0.6)


def test_cache_hit_costs_nothing(tmp_path):
    # A zero budget proves the cached answer is served without a network call.
    cache = Cache(tmp_path / "c.sqlite")
    messages = [{"role": "user", "content": "hi"}]
    key = Client(Budget(0.0), pricing={}, dry_run=True, cache=cache).complete("m", messages).request_hash
    cache.put(key, {"choices": [{"message": {"content": '{"x": 1}'}}], "usage": {"cost": 0.5}})
    hit = Client(Budget(0.0), pricing={"m": (1.0, 1.0)}, cache=cache).complete("m", messages)
    assert hit.cached and hit.cost_usd == 0.0 and hit.json() == {"x": 1}


def test_spearman():
    from mpt.evaluate import spearman
    assert spearman({"a": 1, "b": 2, "c": 3}, {"a": 10, "b": 20, "c": 30}) == 1.0
    assert spearman({"a": 1, "b": 2, "c": 3}, {"a": 30, "b": 20, "c": 10}) == -1.0
