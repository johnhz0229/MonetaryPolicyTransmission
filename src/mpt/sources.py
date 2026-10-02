"""Fetchers for the ECB Data Portal, Eurostat and ECB press releases."""

from __future__ import annotations

import csv
import html
import io
import re

import requests

ECB_API = "https://data-api.ecb.europa.eu/service/data"
EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
TIMEOUT = 60


def fetch_ecb(key: str, start: str | None = None) -> list[tuple[str, float]]:
    """Return [(period, value)] for an ECB series key such as 'FM/D.U2.EUR.4F.KR.DFR.LEV'."""
    params = {"format": "csvdata"}
    if start:
        params["startPeriod"] = start
    resp = requests.get(f"{ECB_API}/{key}", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = csv.DictReader(io.StringIO(resp.text))
    return [(r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in rows if r["OBS_VALUE"] not in ("", "NaN")]


def fetch_hicp(geos: list[str], start: str = "2024-01") -> dict[str, list[tuple[str, float]]]:
    """Annual HICP inflation (ECOICOP 2, all items) by geo from Eurostat's prc_hicp_minr."""
    params = [("unit", "RCH_A"), ("coicop18", "TOTAL"), ("sinceTimePeriod", start)]
    params += [("geo", g) for g in geos]
    resp = requests.get(f"{EUROSTAT_API}/prc_hicp_minr", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_jsonstat(resp.json())


def parse_jsonstat(d: dict) -> dict[str, list[tuple[str, float]]]:
    """Split a Eurostat JSON-stat response with geo and time dimensions into per-geo series."""
    ids, sizes = d["id"], d["size"]
    geo_index = d["dimension"]["geo"]["category"]["index"]
    time_index = d["dimension"]["time"]["category"]["index"]
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    g_stride, t_stride = strides[ids.index("geo")], strides[ids.index("time")]
    out: dict[str, list[tuple[str, float]]] = {}
    for geo, gi in geo_index.items():
        series = []
        for period, ti in sorted(time_index.items(), key=lambda kv: kv[1]):
            value = d["value"].get(str(gi * g_stride + ti * t_stride))
            if value is not None:
                series.append((period, float(value)))
        out[geo] = series
    return out


def fetch_press_release_text(url: str) -> str:
    """Plain text of an ECB press release page (the <main> element)."""
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    page = resp.content.decode("utf-8", errors="replace")
    m = re.search(r"<main.*?</main>", page, re.S)
    body = re.sub(r"<(script|style).*?</\1>", "", m.group(0) if m else page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    # Drop the trailing contact/footer block.
    return text.split("\nFor media queries")[0].strip()
