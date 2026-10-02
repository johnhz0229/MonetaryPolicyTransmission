"""Agent archetypes, prompts and answer schemas.

Prompts carry only facts from the point-in-time information set; no narrative
written by the researchers is added, so agents cannot pick up hindsight from us.
"""

from __future__ import annotations

from dataclasses import dataclass

# BLS answer scale for credit standards (same wording and coding as the ECB questionnaire).
STANDARDS_SCALE = {
    1: "tightened considerably",
    2: "tightened somewhat",
    3: "remained basically unchanged",
    4: "eased somewhat",
    5: "eased considerably",
}
BLS_ITEMS = {
    "enterprise": "loans or credit lines to enterprises",
    "housing": "loans to households for house purchase",
    "consumer": "consumer credit and other lending to households",
}


@dataclass(frozen=True)
class Bank:
    id: str
    weight: float
    country: str
    description: str


@dataclass(frozen=True)
class Household:
    id: str
    weight: float  # within-country population weight
    country: str   # ISO code
    country_name: str
    age_band: str
    income_quintile: int
    tenure: str
    mortgage_type: str | None


def banks(archetypes: dict) -> list[Bank]:
    out = [Bank(**b) for b in archetypes["banks"]]
    total = sum(b.weight for b in out)
    assert abs(total - 1) < 1e-9, f"bank weights sum to {total}"
    return out


def households(archetypes: dict, countries: list[str]) -> list[Household]:
    h = archetypes["households"]
    out = []
    for c in countries:
        for age, age_weight in h["age_bands"].items():
            for q in range(1, 6):
                tenure = h["tenure"][age][q]
                out.append(Household(
                    id=f"{c}_{age}_q{q}", weight=age_weight / 5, country=c, country_name=h["country_names"][c],
                    age_band=age, income_quintile=q, tenure=tenure,
                    mortgage_type=h["mortgage_type"][c] if tenure == "mortgagor" else None,
                ))
    return out


# ---------------------------------------------------------------- data rendering

def _fmt(obs, n=None, decimals=2):
    obs = obs[-n:] if n else obs
    return ", ".join(f"{p}: {v:.{decimals}f}" for p, v in obs) or "(no data published yet)"


RELEASE_MONTH = {"1": "January", "2": "April", "3": "July", "4": "October"}


def _fmt_bls(obs, n=4):
    """BLS rounds are labelled by release quarter in the ECB database; show the release month instead."""
    return ", ".join(f"{RELEASE_MONTH[p[-1]]} {p[:4]} release: {round(v):d}" for p, v in obs[-n:]) or "(no data published yet)"


def render_data(info: dict, hicp_geos=("EA", "DE", "FR", "IT", "ES")) -> str:
    s = info["series"]
    lines = [f"Data published by {info['asof']} (all figures in %):", ""]
    lines.append("ECB deposit facility rate, changes since 2024 (effective dates): " + _fmt(s["dfr"]["obs"]))
    lines.append("EUR STR, latest: " + _fmt(s["estr"]["obs"], 1, 3))
    lines.append("12-month Euribor, monthly average, last 6 months: " + _fmt(s["euribor_12m"]["obs"], 6))
    lines.append("")
    lines.append("HICP inflation, annual rate, last 8 months:")
    for g in hicp_geos:
        lines.append(f"  {g}: " + _fmt(s[f"hicp_{g}"]["obs"], 8, 1))
    lines.append("")
    lines.append("Bank lending rates on new business, last 6 months (composite cost of borrowing):")
    for c in ("U2", "DE", "FR", "IT", "ES"):
        label = "euro area" if c == "U2" else c
        lines.append(f"  firms, {label}: " + _fmt(s[f"mir_nfc_{c}"]["obs"], 6))
        lines.append(f"  house purchase, {label}: " + _fmt(s[f"mir_house_{c}"]["obs"], 6))
    lines.append("")
    lines.append("Euro area bank lending survey, recent releases (net % of banks; positive = tightening):")
    for key, label in [
        ("bls_ent_standards", "credit standards, loans to enterprises"),
        ("bls_hh_house_standards", "credit standards, housing loans"),
        ("bls_hh_cons_standards", "credit standards, consumer credit"),
    ]:
        lines.append(f"  {label}, change over the 3 months before the survey: " + _fmt_bls(s[f"{key}_backward"]["obs"]))
        lines.append(f"  {label}, expected change over the following 3 months: " + _fmt_bls(s[f"{key}_expected"]["obs"]))
    return "\n".join(lines)


def render_statements(info: dict) -> str:
    if not info["statements"]:
        return ""
    parts = [f"ECB press release of {d}:\n\n{t.strip()}" for d, t in sorted(info["statements"].items())]
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------- bank agents

def bank_schema() -> dict:
    props = {"main_factors": {"type": "string", "description": "the main factors behind your answers, in 2-4 sentences"}}
    for item in BLS_ITEMS:
        for horizon in ("past_3m", "next_3m"):
            props[f"{item}_{horizon}"] = {"type": "integer", "enum": list(STANDARDS_SCALE)}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def bank_messages(bank: Bank, info: dict, quarter_label: str, past_window: str, next_window: str) -> list[dict]:
    system = (
        f"You are the senior loan officer responsible for credit policy at {bank.description}. You are completing the ECB euro area bank lending survey "
        f"({quarter_label} round) on behalf of your bank. Answer as your bank would, based on its "
        "situation and the information below. Today's date is "
        f"{info['asof']}; you know nothing that happened after it."
    )
    scale = "; ".join(f"{k} = {v}" for k, v in STANDARDS_SCALE.items())
    questions = []
    for item, label in BLS_ITEMS.items():
        questions.append(f"- {item}_past_3m: over the past three months ({past_window}), how have your bank's credit "
                         f"standards as applied to the approval of {label} changed?")
        questions.append(f"- {item}_next_3m: over the next three months ({next_window}), how do you expect your bank's "
                         f"credit standards as applied to the approval of {label} to change?")
    user = "\n\n".join(filter(None, [
        render_data(info),
        render_statements(info),
        "Survey questions. Credit standards are the internal guidelines or loan approval criteria of your bank, "
        "not the interest rate charged. Answer each with a code from this scale: " + scale + ".",
        "\n".join(questions),
        "Reply with a JSON object containing main_factors and the six answer codes.",
    ]))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- household agents

TENURE_TEXT = {
    "renter": "You rent your home.",
    "outright_owner": "You own your home outright, with no mortgage.",
    "mortgagor": "You own your home with a mortgage that is {mortgage_type}.",
}


def household_schema() -> dict:
    props = {
        "reasoning": {"type": "string", "description": "one or two sentences in your own words"},
        "perceived_inflation_past_12m": {"type": "number"},
        "expected_inflation_next_12m": {"type": "number"},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def household_news(hh: Household, info: dict) -> str:
    """Plain-language facts a household could have seen in the news by the as-of date."""
    s = info["series"]
    hicp = s[f"hicp_{hh.country}"]["obs"]
    lines = []
    if hicp:
        period, value = hicp[-1]
        year_ago = next((v for p, v in hicp if p == f"{int(period[:4]) - 1}{period[4:]}"), None)
        line = f"- Official statistics: consumer prices in {hh.country_name} were {value:.1f}% higher in {period} than a year earlier"
        lines.append(line + (f" (a year before, the rate was {year_ago:.1f}%)." if year_ago is not None else "."))
    if info["statements"]:
        date, text = sorted(info["statements"].items())[-1]
        first = next(p for p in text.split("\n\n") if p.startswith("The Governing Council today"))
        lines.append(f"- European Central Bank announcement on {date}: \"{first.strip()}\"")
    if hh.tenure == "mortgagor":
        house = s[f"mir_house_{hh.country}"]["obs"]
        if len(house) >= 13:
            lines.append(f"- Banks in {hh.country_name} charged on average {house[-1][1]:.2f}% on new mortgages in "
                         f"{house[-1][0]}, compared with {house[-13][1]:.2f}% a year earlier.")
    return "\n".join(lines)


def household_messages(hh: Household, info: dict, arm: str) -> list[dict]:
    tenure = TENURE_TEXT[hh.tenure].format(mortgage_type=hh.mortgage_type)
    system = (
        f"You are a person aged {hh.age_band} living in {hh.country_name}, with a household income in the "
        f"{['lowest', 'second', 'middle', 'fourth', 'highest'][hh.income_quintile - 1]} fifth of households in your "
        f"country. {tenure} You are taking part in a monthly consumer survey. Answer as this person honestly would, "
        f"from your own experience of prices; you are not an economist. Today's date is {info['asof']}; "
        "you know nothing that happened after it."
    )
    parts = []
    if arm == "news":
        parts.append("Things you may have seen in the news recently:\n" + household_news(hh, info))
    parts.append(
        f"Q1. By how much, in percent, do you think consumer prices in general in {hh.country_name} have changed over "
        "the past 12 months? (perceived_inflation_past_12m; a negative number means prices fell)\n"
        f"Q2. By how much, in percent, do you think consumer prices in general in {hh.country_name} will change over "
        "the next 12 months? (expected_inflation_next_12m)\n"
        "Reply with a JSON object containing reasoning, perceived_inflation_past_12m and expected_inflation_next_12m."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]
