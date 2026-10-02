"""OpenRouter chat client with a response cache and a hard spending cap.

Authentication: a local run reads OPENROUTER_API_KEY (or .env). In a claude.ai
cloud session the key is stored as an environment API credential for
openrouter.ai and the agent proxy adds the Authorization header after the
request leaves the container, so no key is present here and none is sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field

import requests

from . import CACHE_DIR, ROOT

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"


class CreditsExhausted(RuntimeError):
    """OpenRouter returned 402: the account has no credits left. Stops the run like BudgetExceeded."""


class BudgetExceeded(RuntimeError):
    pass


def api_key() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return None


def fetch_pricing() -> dict[str, tuple[float, float]]:
    """USD per token (prompt, completion) for every OpenRouter model. Public endpoint."""
    data = requests.get(MODELS_URL, timeout=60).json()["data"]
    return {m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])) for m in data}


class Budget:
    """Thread-safe spending cap. A call reserves its worst-case cost before it is sent."""

    def __init__(self, limit_usd: float):
        self.limit_usd = limit_usd
        self.spent_usd = 0.0
        self.reserved_usd = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimate_usd: float) -> None:
        with self._lock:
            if self.spent_usd + self.reserved_usd + estimate_usd > self.limit_usd:
                raise BudgetExceeded(
                    f"next call (~${estimate_usd:.4f}) would exceed the ${self.limit_usd:.2f} budget "
                    f"(spent ${self.spent_usd:.4f}, in flight ${self.reserved_usd:.4f})"
                )
            self.reserved_usd += estimate_usd

    def settle(self, estimate_usd: float, cost_usd: float) -> None:
        with self._lock:
            self.reserved_usd -= estimate_usd
            self.spent_usd += cost_usd


@dataclass
class Completion:
    model: str
    content: str
    cost_usd: float
    usage: dict
    cached: bool
    request_hash: str
    raw: dict = field(repr=False, default_factory=dict)

    def json(self) -> dict:
        return parse_json(self.content)


def parse_json(text: str) -> dict:
    """Parse a JSON object, tolerating code fences or prose around it."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


class Cache:
    def __init__(self, path=CACHE_DIR / "llm_cache.sqlite"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS responses (hash TEXT PRIMARY KEY, response TEXT)")

    def get(self, key: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT response FROM responses WHERE hash = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, response: dict) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, json.dumps(response)))
            self.db.commit()


class Client:
    """dry_run=True never touches the network and returns schema-shaped placeholder JSON."""

    def __init__(self, budget: Budget, pricing: dict | None = None, dry_run: bool = False, cache: Cache | None = None):
        self.budget = budget
        self.dry_run = dry_run
        self.pricing = pricing if pricing is not None else ({} if dry_run else fetch_pricing())
        self.cache = cache or Cache()
        self.session = requests.Session()

    def estimate(self, model: str, messages: list[dict], max_tokens: int) -> float:
        prompt_price, completion_price = self.pricing.get(model, (0.0, 0.0))
        prompt_tokens = sum(len(m["content"]) for m in messages) / 3.5
        return prompt_tokens * prompt_price + max_tokens * completion_price

    def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        schema: dict | None = None,
        temperature: float = 1.0,
        max_tokens: int = 1000,
        sample: int = 0,
        extra: dict | None = None,
    ) -> Completion:
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                   "seed": sample, "usage": {"include": True}}
        if schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
            # Route only to providers that honour every parameter (structured outputs, seed).
            payload["provider"] = {"require_parameters": True}
        payload.update(extra or {})
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

        cached = self.cache.get(key)
        if cached is not None:
            return self._completion(model, cached, key, cached=True)
        if self.dry_run:
            return Completion(model, json.dumps(placeholder(schema)), 0.0, {}, False, key)

        estimate = self.estimate(model, messages, max_tokens)
        self.budget.reserve(estimate)
        try:
            response = self._post(payload)
        except Exception:
            self.budget.settle(estimate, 0.0)
            raise
        self.cache.put(key, response)
        completion = self._completion(model, response, key, cached=False)
        self.budget.settle(estimate, completion.cost_usd)
        return completion

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json", "X-Title": "MonetaryPolicyTransmission"}
        key = api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        for attempt in range(6):
            resp = self.session.post(API_URL, headers=headers, json=payload, timeout=180)
            if resp.status_code in (408, 429) or resp.status_code >= 500:
                time.sleep(min(60, 2 ** attempt + random.random()))
                continue
            if resp.status_code == 402:
                raise CreditsExhausted("OpenRouter returned 402: the account has no credits left; top up and re-run.")
            if resp.status_code == 401:
                raise RuntimeError("OpenRouter returned 401: no API key reached the API (see README, 'API key').")
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(f"OpenRouter error: {body['error']}")
            return body
        resp.raise_for_status()
        raise RuntimeError("OpenRouter request failed after retries")

    def _completion(self, model: str, response: dict, key: str, cached: bool) -> Completion:
        usage = response.get("usage") or {}
        cost = usage.get("cost")
        if cost is None:
            prompt_price, completion_price = self.pricing.get(model, (0.0, 0.0))
            cost = usage.get("prompt_tokens", 0) * prompt_price + usage.get("completion_tokens", 0) * completion_price
        content = response["choices"][0]["message"].get("content") or ""
        return Completion(response.get("model", model), content, 0.0 if cached else float(cost), usage, cached, key, response)


def placeholder(schema: dict | None) -> dict:
    """Minimal instance of a JSON schema, for dry runs."""
    if not schema:
        return {}
    out = {}
    for name, prop in schema.get("properties", {}).items():
        t = prop.get("type")
        t = t[0] if isinstance(t, list) else t
        if "enum" in prop:
            out[name] = prop["enum"][len(prop["enum"]) // 2]
        elif t in ("integer", "number"):
            out[name] = prop.get("minimum", 0)
        elif t == "boolean":
            out[name] = False
        elif t == "object":
            out[name] = placeholder(prop)
        else:
            out[name] = "dry-run"
    return out
