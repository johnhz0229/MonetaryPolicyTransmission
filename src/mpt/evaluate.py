"""Baselines, freezing predictions before the data are released, and scoring afterwards."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

from . import DATA_DIR, ROOT, aggregate, snapshot, sources

BLS_TARGETS = {
    # forecast key: (ECB series key, label)
    "enterprise_past_3m": ("BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET", "credit standards, enterprises, past 3 months"),
    "housing_past_3m": ("BLS/Q.U2.ALL.Z.H.H.B3.ST.S.WFNET", "credit standards, housing loans, past 3 months"),
    "consumer_past_3m": ("BLS/Q.U2.ALL.Z.H.C.B3.ST.S.WFNET", "credit standards, consumer credit, past 3 months"),
    "enterprise_next_3m": ("BLS/Q.U2.ALL.O.E.Z.F3.ST.S.WFNET", "credit standards, enterprises, next 3 months"),
    "housing_next_3m": ("BLS/Q.U2.ALL.Z.H.H.F3.ST.S.WFNET", "credit standards, housing loans, next 3 months"),
    "consumer_next_3m": ("BLS/Q.U2.ALL.Z.H.C.F3.ST.S.WFNET", "credit standards, consumer credit, next 3 months"),
}
# Which published series each baseline copies forward.
BLS_BASELINE_SOURCE = {
    "persistence": {k: k for k in BLS_TARGETS},
    # Banks' own expectation from the previous round, for the backward-looking targets.
    "banks_previous_expectation": {f"{i}_past_3m": f"{i}_next_3m" for i in ("enterprise", "housing", "consumer")},
}
PRIMARY_CES_ARM = "news"
BASELINES_PATH = DATA_DIR / "baselines.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except subprocess.CalledProcessError:
        return "uncommitted"


def build_baselines(cfg: dict) -> dict:
    """Latest published value of every target series, fetched now (before the target release)."""
    bls_last = {}
    for name, (key, _) in BLS_TARGETS.items():
        period, value = sources.fetch_ecb(key, start="2025-01")[-1]
        bls_last[name] = {"period": period, "value": value}
    ces_last = {}
    for country in cfg["targets"]["ces"]["countries"]:
        key = cfg["targets"]["ces"]["series_template"].format(country=country)
        period, value = sources.fetch_ecb(key, start="2026-01")[-1]
        ces_last[country] = {"period": period, "value": value}
    baselines = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bls_last_published": bls_last,
        "bls": {name: {t: bls_last[src]["value"] for t, src in mapping.items()} for name, mapping in BLS_BASELINE_SOURCE.items()},
        "ces_last_published": ces_last,
        "ces": {"persistence": {c: v["value"] for c, v in ces_last.items()}},
    }
    BASELINES_PATH.write_text(json.dumps(baselines, indent=1))
    return baselines


def summarise(out_dir: Path) -> dict:
    return {
        "probe": aggregate.aggregate_probe(aggregate.load(out_dir / "probe.jsonl")),
        "bls": aggregate.aggregate_bls(aggregate.load(out_dir / "bls.jsonl")),
        "ces": aggregate.aggregate_ces(aggregate.load(out_dir / "ces.jsonl")),
    }


def published_targets(cfg: dict) -> list[str]:
    """Target series that already contain the forecast period (non-empty means it is too late to freeze)."""
    found = []
    period = cfg["targets"]["bls"]["expected_period"]
    for name, (key, _) in BLS_TARGETS.items():
        if period in dict(sources.fetch_ecb(key, start="2026-01")):
            found.append(f"BLS {name} {period}")
    wave, template = cfg["targets"]["ces"]["wave"], cfg["targets"]["ces"]["series_template"]
    for country in cfg["targets"]["ces"]["countries"]:
        if wave in dict(sources.fetch_ecb(template.format(country=country), start="2026-01")):
            found.append(f"CES {country} {wave}")
    return found


def freeze(cfg: dict, out_dir: Path) -> Path:
    """Write predictions.json plus a SHA-256 manifest of every input and output file."""
    too_late = published_targets(cfg)
    if too_late:
        raise RuntimeError(f"targets already published, refusing to freeze: {too_late}")
    baselines = json.loads(BASELINES_PATH.read_text()) if BASELINES_PATH.exists() else build_baselines(cfg)
    predictions = {
        "experiment_id": cfg["experiment_id"],
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "code_commit": git_head(),
        "config": cfg,
        "targets": {"bls": {k: v[0] for k, v in BLS_TARGETS.items()}, "bls_period": cfg["targets"]["bls"]["expected_period"],
                    "ces_wave": cfg["targets"]["ces"]["wave"], "primary_ces_arm": PRIMARY_CES_ARM},
        "forecasts": summarise(out_dir),
        "baselines": baselines,
    }
    path = out_dir / "predictions.json"
    path.write_text(json.dumps(predictions, indent=1, default=str))
    files = sorted(out_dir.glob("*.jsonl")) + [path] + sorted(snapshot.SNAPSHOT_DIR.glob("*.json")) + [BASELINES_PATH]
    manifest = {str(f.relative_to(ROOT)): sha256(f) for f in files}
    (out_dir / "MANIFEST.sha256").write_text("".join(f"{h}  {p}\n" for p, h in manifest.items()))
    return path


def spearman(x: dict, y: dict) -> float | None:
    """Spearman rank correlation over common keys (no tie correction; fine for 4 countries)."""
    keys = [k for k in x if y.get(k) is not None and x.get(k) is not None]
    if len(keys) < 3:
        return None
    rank = lambda d: {k: r for r, k in enumerate(sorted(keys, key=lambda k: d[k]))}
    rx, ry = rank(x), rank(y)
    n = len(keys)
    return round(1 - 6 * sum((rx[k] - ry[k]) ** 2 for k in keys) / (n * (n * n - 1)), 3)


def score(predictions_path: Path) -> dict:
    """Compare frozen forecasts and baselines with the released data. Run only after release."""
    pred = json.loads(predictions_path.read_text())
    period = pred["targets"]["bls_period"]
    actual_bls = {}
    for name, key in pred["targets"]["bls"].items():
        obs = dict(sources.fetch_ecb(key, start="2026-01"))
        actual_bls[name] = obs.get(period)
    wave = pred["targets"]["ces_wave"]
    template = pred["config"]["targets"]["ces"]["series_template"]
    actual_ces = {c: dict(sources.fetch_ecb(template.format(country=c), start="2026-01")).get(wave)
                  for c in pred["config"]["targets"]["ces"]["countries"]}

    def mae(forecast: dict, actual: dict, suffix: str = "") -> float | None:
        errs = [abs(forecast[k] - actual[k]) for k in actual
                if k.endswith(suffix) and actual[k] is not None and forecast.get(k) is not None]
        return round(sum(errs) / len(errs), 3) if errs else None

    def bls_scores(forecast: dict) -> dict:
        # Backward- and forward-looking targets are scored separately so that baselines
        # covering only one group stay comparable with the models.
        return {"mae_past_3m": mae(forecast, actual_bls, "past_3m"), "mae_next_3m": mae(forecast, actual_bls, "next_3m")}

    def direction_hits(forecast: dict, actual: dict, last: dict) -> str:
        pairs = [(forecast[k] - last[k], actual[k] - last[k]) for k in actual if actual[k] is not None and k in forecast]
        return f"{sum((f > 0) == (a > 0) for f, a in pairs if a != 0)}/{sum(a != 0 for _, a in pairs)}"

    bls_last = {k: v["value"] for k, v in pred["baselines"]["bls_last_published"].items()}
    ces_last = {k: v["value"] for k, v in pred["baselines"]["ces_last_published"].items()}
    report = {"actual": {"bls": actual_bls, "ces": actual_ces}, "bls": {}, "ces": {}}
    for model, f in pred["forecasts"]["bls"].items():
        forecast = {k: v["net_pct"] for k, v in f.items() if isinstance(v, dict)}
        report["bls"][model] = {**bls_scores(forecast), "direction": direction_hits(forecast, actual_bls, bls_last)}
    for name, forecast in pred["baselines"]["bls"].items():
        report["bls"][f"baseline:{name}"] = bls_scores(forecast)
    for model, arms in pred["forecasts"]["ces"].items():
        for arm, by_country in arms.items():
            if not isinstance(by_country, dict):
                continue
            forecast = {c: v["median"] for c, v in by_country.items()}
            report["ces"][f"{model}[{arm}]"] = {"mae": mae(forecast, actual_ces),
                                                "direction": direction_hits(forecast, actual_ces, ces_last),
                                                "spearman": spearman(forecast, actual_ces)}
    report["ces"]["baseline:persistence"] = {"mae": mae(pred["baselines"]["ces"]["persistence"], actual_ces)}
    return report
