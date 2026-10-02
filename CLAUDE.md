# CLAUDE.md

## Language

- Talk to the user in **Chinese**.
- Everything written to the repository is in **English**: code, comments, docs, config, commit messages and PR text.

## Project

LLM agents (banks and households) forecast euro area monetary policy transmission **before** the real survey data are released, and the forecasts are scored afterwards. Read `README.md` for usage and `PREREGISTRATION.md` for the design.

Round 1 (`ea-2026q3-live`) targets:
- the ECB Bank Lending Survey released **27 Oct 2026** (euro area credit-standards net percentages)
- the ECB Consumer Expectations Survey, **September 2026 wave** (median 12m inflation expectation in DE, FR, IT, ES)

Forecasts must be frozen (`python -m mpt.cli freeze`) **before 26 Oct 2026**. `freeze` refuses to run once any target is published.

## State and next steps

1. The user is adding an OpenRouter key as a cloud-environment API credential for host `openrouter.ai`. The proxy injects it, so `OPENROUTER_API_KEY` is normally unset in cloud sessions; the code then sends no Authorization header. Check that it works with a cheap call first, e.g. `PYTHONPATH=src python -m mpt.cli run --tasks probe --models deepseek/deepseek-v4-flash`.
2. Run the knowledge probe for all models, show the user the results, then run `--tasks bls ces`.
3. Review the outputs with the user (error counts, sanity of answers), then `baselines` and `freeze`. Commit the results, and give the user the SHA-256 of `predictions.json` to post publicly (e.g. OSF).
4. After each release, run `score`.

## Conventions

- Run tests with `python -m pytest`; `pytest` is a dev dependency.
- `budget_usd` in `config/experiment.yaml` is a hard cap per CLI invocation. Do not raise it without asking the user.
- Do not edit `PREREGISTRATION.md` hypotheses, metrics or targets after freezing. Log any change under *Deviations*.
