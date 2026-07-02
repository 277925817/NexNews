#!/usr/bin/env python3
"""Thin CLI adapter for the local Codex Harness.

The command surface is defined by workflows.md. The implementation lives behind
scripts.harness modules so reporting, acceptance, catalog and observability
logic can evolve without widening this entrypoint.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness.executor import *  # noqa: F401,F403
from scripts.harness.executor import STAGES, run_acceptance, run_product_stage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Codex Harness.")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--task-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    if args.stage == "acceptance":
        return run_acceptance(report_dir, args.task_id)
    return run_product_stage(report_dir, args.stage, args.task_id)


if __name__ == "__main__":
    raise SystemExit(main())
