#!/usr/bin/env python3
"""Manage the local long-running RSS app service on port 8010."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
RUN_DIR = ROOT_DIR / ".local" / "rss-service"
SUPERVISOR_PID_PATH = RUN_DIR / "supervisor.pid"
APP_PID_PATH = RUN_DIR / "uvicorn.pid"
LOG_PATH = RUN_DIR / "service.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
HEALTH_TIMEOUT_SECONDS = 30
LOCAL_ACCEPTANCE_ENV_DEFAULTS = {
    "RSS_RUNTIME_MODE": "live",
    "RSS_ALLOW_LIVE_NETWORK": "1",
    "RSS_FETCH_LIVE_ARTICLES": "1",
    "RSS_HTTP_TIMEOUT_SECONDS": "12",
    "RSS_HTTP_RETRY_COUNT": "2",
    "RSS_HTTP_RETRY_BACKOFF_SECONDS": "0.5",
    "RSS_LIVE_RSS_CONCURRENCY": "33",
    "RSS_LIVE_LLM_MAX_ITEMS": "3",
    "RSS_LIVE_LLM_CONCURRENCY": "3",
    "RSS_LIVE_LLM_TIMEOUT_SECONDS": "20",
    "RSS_LIVE_LLM_RETRY_COUNT": "0",
    "RSS_LIVE_LLM_MAX_SCORE_ITEMS": "3",
    "RSS_LIVE_LLM_SCORE_CONCURRENCY": "3",
}


def local_acceptance_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    for key, value in LOCAL_ACCEPTANCE_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    return env


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def write_pid(path: Path, pid: int) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def append_log(message: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[local-service] {timestamp} {message}\n")


def run_checked(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def build_frontend() -> None:
    run_checked(["npm", "run", "build"], cwd=FRONTEND_DIR)


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def http_ok(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def wait_for_health(host: str, port: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    root_url = f"http://{host}:{port}/"
    api_url = f"http://{host}:{port}/api/home"
    while time.monotonic() < deadline:
        if http_ok(root_url) and http_ok(api_url):
            return True
        time.sleep(0.5)
    return False


def terminate_pid(pid: int | None, *, timeout_seconds: float = 8) -> bool:
    if not pid_alive(pid):
        return True
    assert pid is not None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return not pid_alive(pid)


def remove_stale_pid_files() -> None:
    for path in (SUPERVISOR_PID_PATH, APP_PID_PATH):
        pid = read_pid(path)
        if pid is None or not pid_alive(pid):
            path.unlink(missing_ok=True)


def start_service(args: argparse.Namespace) -> int:
    remove_stale_pid_files()
    supervisor_pid = read_pid(SUPERVISOR_PID_PATH)
    if pid_alive(supervisor_pid):
        print(f"RSS local service is already running at http://{args.host}:{args.port}/")
        return 0

    if port_is_open(args.host, args.port):
        print(
            f"Port {args.host}:{args.port} is already in use by another process; "
            "stop it first or choose another port.",
            file=sys.stderr,
        )
        return 1

    if not args.no_build:
        build_frontend()

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run-supervisor",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--restart-delay",
        str(args.restart_delay),
    ]
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env=local_acceptance_environment(),
        )
    write_pid(SUPERVISOR_PID_PATH, process.pid)

    if wait_for_health(args.host, args.port, HEALTH_TIMEOUT_SECONDS):
        print(f"RSS local service started: http://{args.host}:{args.port}/")
        print(f"Logs: {LOG_PATH}")
        return 0

    print(f"RSS local service failed health check. Logs: {LOG_PATH}", file=sys.stderr)
    terminate_pid(process.pid)
    remove_stale_pid_files()
    return 1


def stop_service(args: argparse.Namespace) -> int:
    supervisor_pid = read_pid(SUPERVISOR_PID_PATH)
    app_pid = read_pid(APP_PID_PATH)
    supervisor_stopped = terminate_pid(supervisor_pid)
    app_stopped = terminate_pid(app_pid)
    remove_stale_pid_files()
    if supervisor_stopped and app_stopped:
        print("RSS local service stopped.")
        return 0
    print("RSS local service did not stop cleanly; inspect logs.", file=sys.stderr)
    return 1


def service_status(args: argparse.Namespace) -> int:
    remove_stale_pid_files()
    supervisor_pid = read_pid(SUPERVISOR_PID_PATH)
    app_pid = read_pid(APP_PID_PATH)
    root_ok = http_ok(f"http://{args.host}:{args.port}/")
    api_ok = http_ok(f"http://{args.host}:{args.port}/api/home")
    print(f"supervisor_pid={supervisor_pid or '-'} running={pid_alive(supervisor_pid)}")
    print(f"uvicorn_pid={app_pid or '-'} running={pid_alive(app_pid)}")
    print(f"root_url=http://{args.host}:{args.port}/ ok={root_ok}")
    print(f"api_url=http://{args.host}:{args.port}/api/home ok={api_ok}")
    print(f"logs={LOG_PATH}")
    return 0 if pid_alive(supervisor_pid) and root_ok and api_ok else 1


def print_logs(args: argparse.Namespace) -> int:
    if not LOG_PATH.exists():
        print(f"No log file yet: {LOG_PATH}")
        return 0
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines:]:
        print(line)
    return 0


def run_supervisor(args: argparse.Namespace) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    write_pid(SUPERVISOR_PID_PATH, os.getpid())
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    append_log(f"supervisor started for {args.host}:{args.port}")

    try:
        while not stop_requested:
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:create_app",
                "--factory",
                "--host",
                args.host,
                "--port",
                str(args.port),
            ]
            append_log("starting uvicorn")
            with LOG_PATH.open("a", encoding="utf-8") as log_file:
                child = subprocess.Popen(
                    command,
                    cwd=ROOT_DIR,
                    stdout=log_file,
                    stderr=log_file,
                    env=local_acceptance_environment(),
                )
            write_pid(APP_PID_PATH, child.pid)

            while child.poll() is None and not stop_requested:
                time.sleep(0.5)

            if stop_requested:
                append_log("stopping uvicorn")
                terminate_pid(child.pid)
                break

            exit_code = child.poll()
            APP_PID_PATH.unlink(missing_ok=True)
            append_log(f"uvicorn exited with code {exit_code}; restarting")
            time.sleep(max(args.restart_delay, 0.5))
    finally:
        APP_PID_PATH.unlink(missing_ok=True)
        SUPERVISOR_PID_PATH.unlink(missing_ok=True)
        append_log("supervisor stopped")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_address_flags(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--host", default=DEFAULT_HOST)
        subparser.add_argument("--port", type=int, default=DEFAULT_PORT)

    start_parser = subparsers.add_parser("start")
    add_address_flags(start_parser)
    start_parser.add_argument("--no-build", action="store_true")
    start_parser.add_argument("--restart-delay", type=float, default=2.0)

    stop_parser = subparsers.add_parser("stop")
    add_address_flags(stop_parser)

    restart_parser = subparsers.add_parser("restart")
    add_address_flags(restart_parser)
    restart_parser.add_argument("--no-build", action="store_true")
    restart_parser.add_argument("--restart-delay", type=float, default=2.0)

    status_parser = subparsers.add_parser("status")
    add_address_flags(status_parser)

    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--lines", type=int, default=80)

    supervisor_parser = subparsers.add_parser("run-supervisor")
    add_address_flags(supervisor_parser)
    supervisor_parser.add_argument("--restart-delay", type=float, default=2.0)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "start":
        return start_service(args)
    if args.command == "stop":
        return stop_service(args)
    if args.command == "restart":
        stop_service(args)
        return start_service(args)
    if args.command == "status":
        return service_status(args)
    if args.command == "logs":
        return print_logs(args)
    if args.command == "run-supervisor":
        return run_supervisor(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
