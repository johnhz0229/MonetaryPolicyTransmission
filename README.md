# MonetaryPolicyTransmission

LLM agents play euro area banks and households. They forecast how those agents will report reacting to monetary policy **before the real survey data are published**, and the forecasts are scored once the data come out.

Round 1 (`ea-2026q3-live`) covers the 2026 energy-shock tightening: the deposit facility rate rose to 2.25% in June and to 2.50% in September. It forecasts two releases that are not yet public:

| Target | Data | Release |
|---|---|---|
| Bank credit standards (enterprises, housing, consumer credit; past and next 3 months) | ECB Bank Lending Survey (BLS), euro area net percentages | 2026-10-27 |
| Median 12-month household inflation expectations (DE, FR, IT, ES) | ECB Consumer Expectations Survey (CES), September 2026 wave | late October 2026 (expected) |

The design, the model gradient and the scoring rules are in [PREREGISTRATION.md](PREREGISTRATION.md).

## Layout

```
config/experiment.yaml   models, sampling, budget, information-set dates, target series
config/archetypes.yaml   9 bank archetypes and household persona rules
src/mpt/sources.py       ECB Data Portal, Eurostat and ECB press release fetchers
src/mpt/snapshot.py      point-in-time information sets (only data published by the as-of date)
src/mpt/agents.py        bank and household prompts and JSON schemas
src/mpt/probe.py         knowledge probe: does a model already know about the 2026 hikes?
src/mpt/llm.py           OpenRouter client with response cache, hard budget cap and offline dry run
src/mpt/aggregate.py     BLS net percentages and CES weighted medians, with bootstrap intervals
src/mpt/evaluate.py      baselines, freezing predictions, scoring after release
data/                    raw data, information-set snapshots, ECB statement text, baselines
results/<experiment>/    per-call records (JSONL), predictions.json, MANIFEST.sha256
```

## Usage

```bash
pip install -e '.[dev]'          # or: export PYTHONPATH=src
python -m mpt.cli data           # download data and build information sets (committed; --refresh to re-download)
python -m mpt.cli estimate       # number of calls and worst-case cost; calls no model
python -m mpt.cli run --dry-run  # whole pipeline offline with placeholder answers
python -m mpt.cli run --tasks probe              # knowledge probe first (cheap)
python -m mpt.cli run --tasks bls ces            # the forecasts
python -m mpt.cli summary        # aggregated results
python -m mpt.cli baselines      # latest published value of every target
python -m mpt.cli freeze         # write predictions.json + SHA-256 manifest; refuses if a target is already out
python -m mpt.cli score          # after release: compare with actual data and baselines
python -m pytest
```

`run` overwrites the JSONL file of each task it runs. Cached responses (in `cache/`, not committed) cost nothing on a re-run.

## API key

Models are called through [OpenRouter](https://openrouter.ai); one key covers every model.

- **claude.ai cloud sessions:** add an API credential to the environment. Set Allowed websites to `openrouter.ai`, the header to `Authorization` with prefix `Bearer`, and the value to the key. The proxy adds the key after the request leaves the container, so it never appears in code, environment variables or logs.
- **Local runs:** copy `.env.example` to `.env` and set `OPENROUTER_API_KEY`. `.env` is git-ignored.

Set a spending limit on the key in the OpenRouter dashboard. The code also enforces `budget_usd`: before each call it reserves the worst-case cost and stops once the cap would be exceeded.
