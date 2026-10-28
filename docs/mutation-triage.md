# Mutation testing — triage, baselines, and gates

Mutation testing pilot for `empower-personal-dashboard`. Plan and decisions:
[Discussion #123](https://github.com/combsmith/empower-personal-dashboard/discussions/123).
Decided posture: `workflow_dispatch` first, moving to a CI check; all pilot
modules; per-module thresholds; equivalent mutants recorded in this file.
Status: promoted to a CI check on every pull request and push to `main`
after the validation cycle in #126.

## How to run

CI (source of truth): the **Mutation Testing** workflow runs the `all`
target on every pull request and push to `main` and fails the
`mutation (all)` check when any gate below is breached. Each run starts from
an empty `mutants/` directory; there is no cache to go stale.

For a single target or a report-only run: GitHub Actions → **Mutation
Testing** → Run workflow, pick `target` (`all`, `models`, `sanitizers`, or
`beancount-helpers`), and set `enforce: false` to report without failing.
Manual runs report as `mutation dispatch (<target>)`, never as the required
`mutation (all)` check.

Locally (Python 3.12, project venv):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[mcp,test,mutation]"
mkdir -p .local-tmp
TMPDIR=$PWD/.local-tmp .venv/bin/mutmut run "empower_personal_dashboard.models*"
.venv/bin/python scripts/check_mutation_thresholds.py --mutants-dir mutants --report-only
```

Use a non-symlinked `TMPDIR` on macOS: the ledger/session path guards refuse
symlinked directories, which otherwise fails unrelated tests. Linux CI is
unaffected.

mutmut 3 is pytest-only. pytest collects the existing `unittest.TestCase`
tests unchanged; no test migration is involved. Config lives in
`[tool.mutmut]` in `pyproject.toml`. Per-module scores come from
`scripts/check_mutation_thresholds.py` (mutmut reports run-wide totals only).
Score follows Stryker's definition: detected / (detected + undetected),
where detected = killed + timeout and undetected = survived + no tests. A
no-tests mutant is a coverage gap, so it counts against the module.
Suspicious, segfault, skipped, and not-checked mutants are reported in the
`other` column but excluded from the score.

## Phase 0 baseline (pre-mutation, Python 3.12)

- Suite: 432 tests. Green in Linux CI. Locally on macOS, 2 `test_client`
  reauthentication tests error because they hardcode `/tmp/dummy_sess.json`
  and macOS `/tmp` is symlinked; this is environmental, not a code failure.
- Coverage: `beancount.py` 95%, `models.py` 98%, `sanitizers.py` 100%.

## Pilot baselines and gates

Measured on branch `feat/mutation-testing-pilot` after test strengthening;
`sanitizers` re-baselined for mutmut 3.8.1 (see below).
The `beancount` score covers only the pilot helper subset (`_safe_float`,
`_safe_positive_float`, `_is_negative_number`, `_clean_tx_description`,
`_has_core_sweep_evidence`, `_is_dividend_signal`, `_classify_buy_sell`,
`_is_commodity_trade_tx`, `_has_investment_account_signal`,
`_is_investment_tx`); the rest of `beancount.py` is not yet mutated.

| Module | Baseline | Killed | Survived | Gate |
|---|---|---|---|---|
| `models` | 100.0% | 548 | 0 | 95 |
| `sanitizers` | 100.0% | 20 | 0 | 90 |
| `beancount` (helpers) | 98.1% | 310 | 6 | 91 |

Gates are baseline minus ~5 points. `sanitizers` stays at 90 after its
re-baseline: with only 20 mutants, each survivor costs 5 points, so a 95 gate
would fail on the second new survivor without leaving room to triage it. They are enforced on every pull request
and push to `main`, and on manual dispatches unless `enforce: false`, via:

```bash
python scripts/check_mutation_thresholds.py --mutants-dir mutants \
  --threshold models=95 --threshold sanitizers=90 --threshold beancount=91
```

Before strengthening, the baselines were: `models` 33.8%, `sanitizers`
95.2%, `beancount` helpers 71.8%. Tests added: exact-dict and full-field
`from_dict` assertions in `tests/test_models.py`, and direct helper tests in
`tests/test_beancount_helpers.py`.

## Equivalent mutants

A mutant is equivalent when the change cannot alter observable behaviour.
Each surviving pilot mutant is listed here, per the committed-file decision.
Re-check this list whenever a listed function changes.

| Mutant | Verdict | Rationale |
|---|---|---|
| `empower_personal_dashboard.beancount.x__classify_buy_sell__mutmut_2` | equivalent | Default `desc_clean` `""` → `"XXXX"` matches no buy/sell phrase, like `""` |
| `empower_personal_dashboard.beancount.x__classify_buy_sell__mutmut_3` | equivalent | Default `cat_clean` `""` → `"XXXX"` contains no `dividend`, like `""` |
| `empower_personal_dashboard.beancount.x__is_commodity_trade_tx__mutmut_47` | equivalent | Missing category fallback `"XXXX"` contains no `dividend`, like `""` |
| `empower_personal_dashboard.beancount.x__has_investment_account_signal__mutmut_8` | equivalent | Missing `account_type` fallback `"XXXX"` contains no signal word, like `""` |
| `empower_personal_dashboard.beancount.x__has_investment_account_signal__mutmut_19` | equivalent | Missing `account_name` fallback token `xxxx` is not an investment token, like `""` |
| `empower_personal_dashboard.beancount.x__is_investment_tx__mutmut_28` | equivalent | Missing category fallback `"XXXX"` contains no `dividend`, like `""` |

The four type-field fallbacks in `_is_investment_tx` / `_is_commodity_trade_tx`
(`__mutmut_8`, `__mutmut_16`, `__mutmut_15`, `__mutmut_23`) were first recorded
as equivalent and are **not**: with both type fields absent, `"XXXX"` flips
`bool(tx_type_clean or inv_type_clean)` in `_is_dividend_signal`, changing
category-only dividend handling. They are killed by
`test_category_dividend_without_any_type_is_not_investment` and
`test_category_dividend_without_type_does_not_block_phrase_trade` in
`tests/test_beancount_helpers.py` (PR #124 review).

mutmut 3.8.1 no longer generates case mutants inside string escape
sequences (boxed/mutmut#609), so the former `sanitizers` equivalent
(`"\ufffd"` → `"\uFFFD"`) is gone and the remaining `x_clean_api_text`
mutants were renumbered. That moved `sanitizers` from 95.2% (20/1) to
100.0% (20/0) with no code change: drift runs on `main` at `a7490f5`, #126.
The `beancount` and `models` mutant sets and statuses were unchanged.

## Tooling and mutant IDs

Mutant IDs (`__mutmut_N`) and counts depend on the mutmut version, so
`mutmut` is pinned exactly in the `mutation` extra in `pyproject.toml`.
Upgrade it in a dedicated PR. Its `mutation (all)` run shows any score
movement. Re-run the triage, then update the baselines and the equivalent
table above in that same PR.

## Triage workflow for new survivors

1. Reproduce with `.venv/bin/mutmut show <mutant>` (use the full `empower_personal_dashboard.…` key) and read the diff.
2. If the diff can change behaviour, write the smallest failing assertion
   first, keep it as a regression test, and re-run the target.
3. If it cannot, add a row above with a one-line rationale.
4. Never add a test that only kills a mutant without asserting a real
   requirement. Record it as equivalent instead.

## Promotion to a CI check

Criteria (all met 2026-10-27, tracked in #126):

- 3 consecutive dispatch runs with stable per-module scores: runs
  [40601096626](https://github.com/combsmith/empower-personal-dashboard/actions/runs/40601096626),
  [40601477260](https://github.com/combsmith/empower-personal-dashboard/actions/runs/40601477260) and
  [40601801177](https://github.com/combsmith/empower-personal-dashboard/actions/runs/40601801177)
  on `main` at `38b2cb7` produced identical counts. A `models`-only enforced run,
  [40602106030](https://github.com/combsmith/empower-personal-dashboard/actions/runs/40602106030),
  confirmed single-target gating.
- Full `all` runtime under 30 minutes: about 2.5 minutes per run.
- This file current: baselines, gates, and equivalents all recorded.

To widen the mutation scope later, first key modules by path (#125), then
record the new module's baseline and gate here and add it to the
workflow's pattern list.
