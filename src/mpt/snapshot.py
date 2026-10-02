"""Download raw data once, then cut point-in-time information sets from it.

Each observation is released only once its (conservative) publication date has
passed, so an agent asked "as of 15 September 2026" never sees August lending
rates that were published in October.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from . import DATA_DIR, sources

RAW_PATH = DATA_DIR / "raw.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
STATEMENT_DIR = DATA_DIR / "statements"

CES_COUNTRIES = ["DE", "FR", "IT", "ES"]
MIR_AREAS = ["U2"] + CES_COUNTRIES


@dataclass(frozen=True)
class Release:
    """When an observation becomes public: lag_months after its period, on release_day."""

    lag_months: int = 0
    release_day: int = 1
    lag_days: int = 0  # daily series


# name -> (source, key, release rule, description)
ECB_SERIES = {
    "dfr": ("FM/D.U2.EUR.4F.KR.DFR.LEV", Release(lag_days=0), "ECB deposit facility rate, % (effective date)"),
    "estr": ("EST/B.EU000A2X2A25.WT", Release(lag_days=1), "euro short-term rate (EUR STR), %"),
    "euribor_12m": ("FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA", Release(lag_months=1, release_day=2), "12-month Euribor, monthly average, %"),
    "bls_ent_standards_backward": ("BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET", Release(release_day=28), "BLS: net % of banks tightening credit standards on loans to enterprises over the past 3 months"),
    "bls_ent_standards_expected": ("BLS/Q.U2.ALL.O.E.Z.F3.ST.S.WFNET", Release(release_day=28), "BLS: net % of banks expecting to tighten credit standards on loans to enterprises over the next 3 months"),
    "bls_hh_house_standards_backward": ("BLS/Q.U2.ALL.Z.H.H.B3.ST.S.WFNET", Release(release_day=28), "BLS: net % tightening credit standards on housing loans, past 3 months"),
    "bls_hh_house_standards_expected": ("BLS/Q.U2.ALL.Z.H.H.F3.ST.S.WFNET", Release(release_day=28), "BLS: net % expecting to tighten credit standards on housing loans, next 3 months"),
    "bls_hh_cons_standards_backward": ("BLS/Q.U2.ALL.Z.H.C.B3.ST.S.WFNET", Release(release_day=28), "BLS: net % tightening credit standards on consumer credit, past 3 months"),
    "bls_hh_cons_standards_expected": ("BLS/Q.U2.ALL.Z.H.C.F3.ST.S.WFNET", Release(release_day=28), "BLS: net % expecting to tighten credit standards on consumer credit, next 3 months"),
}
for _c in MIR_AREAS:
    ECB_SERIES[f"mir_nfc_{_c}"] = (f"MIR/M.{_c}.B.A2I.AM.R.A.2240.EUR.N", Release(lag_months=2, release_day=6), f"composite cost of borrowing for firms, new business, {_c}, %")
    ECB_SERIES[f"mir_house_{_c}"] = (f"MIR/M.{_c}.B.A2C.AM.R.A.2250.EUR.N", Release(lag_months=2, release_day=6), f"composite cost of borrowing for house purchase, new business, {_c}, %")

HICP_RELEASE = Release(lag_months=1, release_day=5)
START = "2024-01-01"


def period_start(period: str) -> dt.date:
    """First day of an SDMX period: '2026-09-15', '2026-09' or '2026-Q3'."""
    if "-Q" in period:
        year, q = period.split("-Q")
        return dt.date(int(year), 3 * int(q) - 2, 1)
    parts = [int(p) for p in period.split("-")]
    return dt.date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)


def release_date(period: str, rule: Release) -> dt.date:
    start = period_start(period)
    if rule.lag_months == 0 and rule.release_day == 1:
        return start + dt.timedelta(days=rule.lag_days)
    month = start.month - 1 + rule.lag_months
    return dt.date(start.year + month // 12, month % 12 + 1, rule.release_day)


def download_raw() -> dict:
    raw = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "series": {}}
    for name, (key, rule, desc) in ECB_SERIES.items():
        obs = sources.fetch_ecb(key, start=START)
        raw["series"][name] = {"source": f"ECB:{key}", "description": desc, "rule": rule.__dict__, "obs": obs}
    for geo, obs in sources.fetch_hicp(["EA"] + CES_COUNTRIES, start=START[:7]).items():
        raw["series"][f"hicp_{geo}"] = {
            "source": "Eurostat:prc_hicp_minr RCH_A TOTAL",
            "description": f"HICP annual inflation, {geo}, %",
            "rule": HICP_RELEASE.__dict__,
            "obs": obs,
        }
    DATA_DIR.mkdir(exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, indent=1))
    return raw


def download_statements(statements: list[dict]) -> None:
    STATEMENT_DIR.mkdir(parents=True, exist_ok=True)
    for s in statements:
        text = sources.fetch_press_release_text(s["url"])
        (STATEMENT_DIR / f"{s['date']}.txt").write_text(f"Source: {s['url']}\n\n{text}\n")


def compress_daily(obs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep only the dates on which a daily step series (e.g. the DFR) changed."""
    out = []
    for period, value in obs:
        if not out or out[-1][1] != value:
            out.append((period, value))
    return out


def information_set(raw: dict, asof: dt.date, statements: list[dict]) -> dict:
    series = {}
    for name, s in raw["series"].items():
        rule = Release(**s["rule"])
        obs = [(p, v) for p, v in s["obs"] if release_date(p, rule) <= asof]
        if name == "dfr":
            obs = compress_daily(obs)
        elif name == "estr":
            obs = obs[-1:]
        series[name] = {"description": s["description"], "source": s["source"], "obs": obs}
    texts = {}
    for s in statements:
        if dt.date.fromisoformat(str(s["date"])) <= asof:
            texts[str(s["date"])] = (STATEMENT_DIR / f"{s['date']}.txt").read_text()
    return {"asof": asof.isoformat(), "raw_fetched_at": raw["fetched_at"], "series": series, "statements": texts}


def build_snapshots(cfg: dict, refresh: bool = False) -> dict[str, dict]:
    raw = download_raw() if refresh or not RAW_PATH.exists() else json.loads(RAW_PATH.read_text())
    if refresh or any(not (STATEMENT_DIR / f"{s['date']}.txt").exists() for s in cfg["statements"]):
        download_statements(cfg["statements"])
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, spec in cfg["information_sets"].items():
        asof = spec["asof"] if isinstance(spec["asof"], dt.date) else dt.date.fromisoformat(spec["asof"])
        info = information_set(raw, asof, cfg["statements"])
        (SNAPSHOT_DIR / f"{name}.json").write_text(json.dumps(info, indent=1))
        out[name] = info
    return out


def load_snapshot(name: str) -> dict:
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text())
