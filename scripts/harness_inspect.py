#!/usr/bin/env python3
"""Inspect local harness reports and observability artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness import inspect


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect local harness evidence.")
    parser.add_argument("--report-dir", default="reports")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("summary", "failures"):
        command = subparsers.add_parser(name)
        command.add_argument("--json", action="store_true")
    gate = subparsers.add_parser("gate")
    gate.add_argument("gate_id")
    gate.add_argument("--json", action="store_true")
    task = subparsers.add_parser("task")
    task.add_argument("task_id")
    task.add_argument("--json", action="store_true")
    trace = subparsers.add_parser("trace")
    trace.add_argument("trace_id")
    trace.add_argument("--json", action="store_true")
    return parser.parse_args()


def payload_for(args: argparse.Namespace) -> dict:
    report_dir = Path(args.report_dir)
    if args.command == "summary":
        return inspect.summary(report_dir)
    if args.command == "failures":
        return {"failures": inspect.failure_records(report_dir)}
    if args.command == "gate":
        return inspect.gate(report_dir, args.gate_id)
    if args.command == "task":
        return inspect.task(report_dir, args.task_id)
    if args.command == "trace":
        return inspect.trace(report_dir, args.trace_id)
    raise ValueError(f"unsupported command: {args.command}")


def main() -> int:
    args = parse_args()
    payload = payload_for(args)
    try:
        print(json.dumps(payload, indent=2 if not args.json else None, ensure_ascii=False))
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
