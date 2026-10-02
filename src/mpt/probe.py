"""Knowledge probe: what does each model already know about 2024-2026 ECB policy?

Control questions (2024-2025) check that a model can answer this kind of question
at all; target questions (2026) check whether the hikes are in its training data.
The boundary question can be answered correctly by assuming no change since mid-2025,
so it is reported but not used to classify a model. Answers come from the ECB deposit facility rate series.
"""

from __future__ import annotations

PROBE_QUESTIONS = [
    # id, question, expected value, kind
    ("dfr_2024_06", "What level did the ECB set its deposit facility rate to with effect from 12 June 2024?", 3.75, "control"),
    ("dfr_2025_06", "What level did the ECB set its deposit facility rate to with effect from 11 June 2025?", 2.00, "control"),
    ("dfr_2026_01", "What was the ECB deposit facility rate on 1 January 2026?", 2.00, "boundary"),
    ("dfr_2026_06", "What was the ECB deposit facility rate on 30 June 2026?", 2.25, "target"),
    ("dfr_2026_09", "What was the ECB deposit facility rate on 30 September 2026?", 2.50, "target"),
    ("first_hike_2026", "In which month of 2026 did the ECB first change its deposit facility rate, and to what level?", 2.25, "target"),
]

SYSTEM = (
    "Answer factual questions about European Central Bank policy from your own knowledge. "
    "If you do not know the answer, or the date is after your knowledge ends, say so: set value to null "
    "and known to false. Do not guess."
)


def probe_schema() -> dict:
    props = {
        "answer": {"type": "string", "description": "short answer in words"},
        "value": {"type": ["number", "null"], "description": "the deposit facility rate in percent, or null"},
        "known": {"type": "boolean"},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def probe_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]


def score(answer: dict, expected: float) -> bool:
    value = answer.get("value")
    return bool(answer.get("known")) and value is not None and abs(float(value) - expected) < 0.01
