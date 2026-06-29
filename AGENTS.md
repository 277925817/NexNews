# AGENTS.md

## Role

This file is the lightweight project commander for Codex agents.

Codex must use:

- `workflows.md` for workflow state transitions.
- `tasks.md` for task queue and task status.
- `harness.md` for local harness commands and report paths.
- `docs/01_prd.md` through `docs/08_acceptance.md` for product, architecture, UI, data, API, development, test and acceptance truth.

## Command Rule

Always start from `workflows.md`.

The project is complete only when `docs/08_acceptance.md` evaluates all required gates as `PASS` and `STOP_ALLOWED = true`.

## Priority Rule

When documents conflict, apply this order:

1. `docs/05_api_contract.md`
2. `docs/04_data_model.md`
3. `docs/06_dev_rules.md`
4. `docs/03_ui_spec.md`
5. `docs/01_prd.md`
6. `docs/02_arch.md`

`docs/07_test_spec.md` defines how to test. `docs/08_acceptance.md` defines when to stop.

## Scope Rule

Keep this file small. Do not duplicate product requirements, architecture details, API fields, test cases or acceptance gates here.
