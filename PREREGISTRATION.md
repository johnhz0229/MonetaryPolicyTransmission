# Preregistration: LLM agents forecasting euro area monetary policy transmission (round 1)

**Status: DRAFT, not yet frozen.** Once frozen, this file and `results/ea-2026q3-live/predictions.json` are committed together. The SHA-256 of `predictions.json` is published before the first target release on 27 October 2026. Changes made after freezing are listed under *Deviations*.

## 1. Question

Can LLM agents that play euro area banks and households forecast how those agents actually reported reacting to the 2026 energy-shock tightening, **before** the survey results are published? Does any skill come from reasoning or from memorised training data?

The ECB raised the deposit facility rate (DFR) from 2.00% to 2.25% (effective 17 June 2026) and to 2.50% (effective 16 September 2026). The models in the panel bracket these events: some were released before the shock, some after.

## 2. Hypotheses

- **H1 (banks).** Weighted LLM bank agents forecast the six euro area BLS credit-standards net percentages in the October 2026 release with lower mean absolute error (MAE) than (a) persistence and (b) banks' own expectations in the previous round.
- **H2 (households).** LLM household agents (the `news` arm) forecast the September 2026 CES median 12-month inflation expectation in DE, FR, IT and ES with lower MAE than persistence, and rank the four countries in the same order as the data.
- **H3 (contamination).** If the skill comes from reasoning, it should not rise with model release date. In the `minimal` arm, which gives no 2026 information, models released before the shock should not produce 2026-level answers.

We expect H1 and H2 to be hard to satisfy: persistence is a strong baseline for both surveys. A null result will be reported in the same way as a positive one.

## 3. Models (`config/experiment.yaml`)

All models are called through OpenRouter using pinned, dated versions. A model's release date is a hard upper bound on what its training data can contain.

| Model | Released | Arm |
|---|---|---|
| qwen/qwen3-235b-a22b-2507 | 2025-07-21 | clean |
| deepseek/deepseek-v3.2 | 2025-12-01 | clean |
| deepseek/deepseek-v4-flash | 2026-04-24 | pre-hike |
| deepseek/deepseek-v4.1-flash | 2026-09-10 | post-hike (contamination risk) |

**Knowledge probe** (`src/mpt/probe.py`), run before forecasting: the models are asked the DFR level on dates in 2024-2026, both at temperature 0 and with samples at temperature 1. Every probe result is reported. A model is flagged "knows 2026" if it answers any target question correctly. This flag is a diagnostic only; flagged models are not excluded.

## 4. Information sets (`src/mpt/snapshot.py`)

Agents see only data whose conservative publication date is on or before the as-of date, and no narrative written by the researchers.

| Task | As of | Contents |
|---|---|---|
| CES households | 2026-09-15 (September wave fieldwork) | own-country HICP, the 10 Sept 2026 ECB press release (first paragraph), own-country mortgage rate (mortgagors only) |
| BLS banks | 2026-09-25 (Q3 round fieldwork) | DFR history, EUR STR, 12m Euribor, HICP (EA, DE, FR, IT, ES), MFI lending rates, previous BLS rounds, full 10 Sept 2026 press release |

Publication lags: HICP is available from day 5 of the following month; MFI interest rates from day 6 two months later; a BLS round is available from day 28 of the first month of its SDW period; daily rates from the next day.

## 5. Agents and sampling (`config/archetypes.yaml`, `src/mpt/agents.py`)

**Banks.** There are nine archetypes across DE, FR, IT, ES, NL and AT. Their weights are stylised shares of euro area lending (0.07-0.15, summing to 1). Each archetype answers the BLS credit-standards questions for enterprises, housing loans and consumer credit, over the past 3 months (July-September) and the next 3 months (October-December), on the ECB's 5-point scale. There are 30 samples per archetype per model at temperature 1.

**Households.** Each of the four countries has 15 personas: age band (18-34, 35-54, 55-70, weighted 0.27/0.38/0.35) × income quintile (equal weights). Tenure follows stylised rules; mortgagors hold the country's dominant mortgage type. Each persona reports perceived past and expected next-12-month inflation in percent. There are two arms:
- `news`: the facts listed in section 4. This is the **primary** arm.
- `minimal`: persona and date only. This is a contamination diagnostic.

There are 8 samples per persona per arm per model.

## 6. Aggregation (`src/mpt/aggregate.py`)

- **BLS net percentage** = Σ_b w_b × (share of b's samples answering 1-2 − share answering 4-5) × 100. A 90% interval comes from 1,000 bootstrap draws that resample answers within each bank.
- **CES statistic**: weighted median of `expected_inflation_next_12m` across all persona samples in a country. Each sample's weight is the persona weight divided by that persona's sample count. Answers with |x| > 100 are dropped and counted. A 90% interval comes from the bootstrap.
- Failed calls (API or JSON errors) are counted and reported, not imputed.

## 7. Targets and baselines (`src/mpt/evaluate.py`)

| Target | ECB series | Period |
|---|---|---|
| enterprise_past_3m | BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET | 2026-Q4 (27 Oct release) |
| housing_past_3m | BLS/Q.U2.ALL.Z.H.H.B3.ST.S.WFNET | 2026-Q4 |
| consumer_past_3m | BLS/Q.U2.ALL.Z.H.C.B3.ST.S.WFNET | 2026-Q4 |
| enterprise_next_3m | BLS/Q.U2.ALL.O.E.Z.F3.ST.S.WFNET | 2026-Q4 |
| housing_next_3m | BLS/Q.U2.ALL.Z.H.H.F3.ST.S.WFNET | 2026-Q4 |
| consumer_next_3m | BLS/Q.U2.ALL.Z.H.C.F3.ST.S.WFNET | 2026-Q4 |
| CES DE/FR/IT/ES | CES/M.{c}.ALL.T.C1120.NUM_VAR.WM | 2026-09 |

The SDW database labels a BLS round by its release quarter: the July 2026 release is stored as 2026-Q3. If the 27 October release is stored under a different label, the round published that day is the target, and this is logged as a deviation.

Baselines are fetched before freezing and saved in `data/baselines.json`:
- BLS: *persistence*, the previous round's value of the same series. *Banks' previous expectation* is the previous round's next-3-months value, used for the three past-3-months targets.
- CES: *persistence*, the August 2026 value.

## 8. Metrics and decision rules

- BLS: MAE in percentage points, computed separately over the three past-3-months and the three next-3-months targets. Each model is compared with each baseline on the same targets. Direction hits count how often the sign of the change from the previous round is right, ignoring targets whose actual change is zero.
- CES: MAE over the four countries, direction hits, and Spearman rank correlation across countries.
- A model "beats" a baseline on a task if its MAE is lower. With 3-6 targets in a single release this is a weak test. Round 1 is the first of repeated live rounds, and conclusions are drawn across rounds.
- H3 is assessed by comparing MAE across the release-date gradient and by comparing `minimal`-arm medians with the August 2026 CES values.

## 9. Timeline

1. Probe and forecasts are run once the API key is configured, by mid-October 2026.
2. `mpt freeze` runs after the last forecast and before 26 October 2026. It refuses to run if any target is already published. The predictions hash is then posted publicly (e.g. OSF).
3. BLS is released on 27 October 2026; CES September wave around late October; `mpt score` follows each release.

## 10. Known limitations

- HICP comes from Eurostat's current vintage, not the real-time vintage. Revisions are usually ±0.1 pp.
- Prompts are in English, whereas the CES and the BLS are fielded in national languages.
- Bank weights and household tenure rules are stylised, not estimated.
- The CES country medians are survey statistics with their own sampling noise.
- The OpenRouter provider serving a model can vary. The served model id is recorded for every call.

## Deviations

Changes made before freezing, recorded for transparency:

- **2026-10-02, knowledge probe prompt.** The first probe run used a system prompt ending "say so: set value to null and known to false. Do not guess." Qwen3-235B then refused even the 2024 control question (one answer read "set value to null"), so its probe was uninformative. The prompt now asks the model to answer from its training knowledge and to return null only when the date lies beyond its training data or it does not know. Questions, expected values and the classification rule are unchanged. No model answered a 2026 target question correctly under either prompt.
- **2026-10-02, token limits.** DeepSeek V4 models spend tokens on reasoning, which count towards `max_tokens`. With the original caps (400 for the probe, 1,200 for forecasts) some probe calls hit the cap before producing an answer. The caps are now 2,000 (probe) and 3,000 (forecasts). No forecast had been run under the old cap.
