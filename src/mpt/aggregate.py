"""Turn agent answers into survey statistics comparable with the ECB's published ones."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from .agents import BLS_ITEMS
from .probe import score

BOOTSTRAP_DRAWS = 1000


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def weighted_median(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    total, cum = sum(weights), 0.0
    for value, weight in pairs:
        cum += weight
        if cum >= total / 2:
            return value
    raise ValueError("empty input")


def net_percentage(codes_by_bank: dict[str, list[int]], weights: dict[str, float]) -> float:
    """BLS net percentage: weighted share tightening (codes 1-2) minus share easing (4-5), in percent."""
    net = 0.0
    total_weight = sum(weights[b] for b in codes_by_bank)
    for bank, codes in codes_by_bank.items():
        tighten = sum(c <= 2 for c in codes) / len(codes)
        ease = sum(c >= 4 for c in codes) / len(codes)
        net += weights[bank] / total_weight * (tighten - ease)
    return 100 * net


def _bootstrap(stat, groups: dict[str, list], rng: random.Random) -> tuple[float, float]:
    """5th-95th percentile of a statistic, resampling answers within each group."""
    draws = sorted(stat({g: [rng.choice(v) for _ in v] for g, v in groups.items()}) for _ in range(BOOTSTRAP_DRAWS))
    return draws[int(0.05 * BOOTSTRAP_DRAWS)], draws[int(0.95 * BOOTSTRAP_DRAWS) - 1]


def aggregate_probe(records: list[dict]) -> dict:
    out: dict = defaultdict(dict)
    for r in records:
        entry = out[r["model"]].setdefault(r["unit"], {"kind": r["kind"], "expected": r["expected"], "answers": [], "errors": 0})
        if "error" in r:
            entry["errors"] += 1
            continue
        entry["answers"].append({"sample": r["sample"], "value": r["answer"].get("value"),
                                 "known": r["answer"].get("known"), "correct": score(r["answer"], r["expected"])})
    for model, questions in out.items():
        knows = any(a["correct"] for q in questions.values() if q["kind"] == "target" for a in q["answers"])
        questions["_summary"] = {"answers_any_2026_target_correctly": knows}
    return dict(out)


def aggregate_bls(records: list[dict], seed: int = 0) -> dict:
    rng = random.Random(seed)
    by_model: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    weights, errors = {}, defaultdict(int)
    for r in records:
        if "error" in r:
            errors[r["model"]] += 1
            continue
        weights[r["unit"]] = r["weight"]
        for item in BLS_ITEMS:
            for horizon in ("past_3m", "next_3m"):
                by_model[r["model"]][f"{item}_{horizon}"][r["unit"]].append(int(r["answer"][f"{item}_{horizon}"]))
    out = {}
    for model, questions in by_model.items():
        out[model] = {"errors": errors[model]}
        for q, groups in questions.items():
            lo, hi = _bootstrap(lambda g: net_percentage(g, weights), groups, rng)
            out[model][q] = {"net_pct": round(net_percentage(groups, weights), 2), "ci90": [round(lo, 2), round(hi, 2)],
                             "n": sum(len(v) for v in groups.values())}
    return out


def aggregate_ces(records: list[dict], seed: int = 0, bound: float = 100.0) -> dict:
    """Weighted median 12m-ahead expectation by model, arm and country. Answers outside +-bound are dropped and counted."""
    rng = random.Random(seed)
    cells: dict = defaultdict(lambda: defaultdict(list))
    weights, errors, dropped = {}, defaultdict(int), defaultdict(int)
    for r in records:
        if "error" in r:
            errors[r["model"]] += 1
            continue
        value = r["answer"].get("expected_inflation_next_12m")
        if not isinstance(value, (int, float)) or abs(value) > bound:
            dropped[r["model"]] += 1
            continue
        weights[r["unit"]] = r["weight"]
        cells[(r["model"], r["arm"], r["country"])][r["unit"]].append(float(value))

    def median(groups):
        values, w = [], []
        for unit, vals in groups.items():
            values += vals
            w += [weights[unit] / len(vals)] * len(vals)
        return weighted_median(values, w)

    out: dict = defaultdict(lambda: {"errors": 0, "dropped": 0})
    for (model, arm, country), groups in sorted(cells.items()):
        lo, hi = _bootstrap(median, groups, rng)
        out[model].setdefault(arm, {})[country] = {"median": round(median(groups), 2), "ci90": [round(lo, 2), round(hi, 2)],
                                                   "n": sum(len(v) for v in groups.values())}
    for model in out:
        out[model]["errors"], out[model]["dropped"] = errors[model], dropped[model]
    return dict(out)
