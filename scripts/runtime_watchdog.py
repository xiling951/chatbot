"""Local service recovery and layered observations; no model/IM test sends."""

import argparse
import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from filelock import FileLock, Timeout
from local_paths import PROJECT_ROOT, DATA, PLUGIN_DATA


def read_json(path: Path):
    raw = path.read_bytes()
    return json.loads(
        raw.decode(
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
    )


def save_state(path: Path, value: dict):
    descriptor, temporary = tempfile.mkstemp(
        prefix="watchdog-state-", suffix=".local.tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def process_start_ticks(pid: int) -> int | None:
    if os.name != "nt" or pid <= 0:
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        *([ctypes.POINTER(wintypes.FILETIME)] * 4),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel_time, user_time = [
            wintypes.FILETIME() for _ in range(4)
        ]
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return None
        offset = (datetime(1601, 1, 1) - datetime(1, 1, 1)).days * 86400 * 10_000_000
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime) + offset
    finally:
        kernel.CloseHandle(handle)


def observe() -> dict:
    sample = {
        "at": datetime.now(timezone.utc).isoformat(),
        "process_alive": False,
        "dashboard_http_ok": False,
        "model_probe": "not_run",
        "device_delivery": "not_observed",
        "channel": {
            "adapter_running": False,
            "configured": False,
            "has_reported_error": None,
        },
    }
    try:
        saved = read_json(DATA / "companion-process.local.json")
        sample["process_alive"] = process_start_ticks(int(saved["id"])) == int(
            saved["started_ticks"]
        )
        if not sample["process_alive"]:
            return sample
        auth = read_json(DATA / "admin.local.json")
        # Credentials only go to the local service whose process identity matched.
        with httpx.Client(
            base_url="http://127.0.0.1:6185", trust_env=False, timeout=4
        ) as client:
            sample["dashboard_http_ok"] = client.get("/").status_code == 200
            if sample["dashboard_http_ok"]:
                login = client.post(
                    "/api/auth/login",
                    json={"username": auth["username"], "password": auth["password"]},
                )
                login.raise_for_status()
                client.headers["Authorization"] = (
                    "Bearer " + login.json()["data"]["token"]
                )
                response = client.get("/api/platform/stats")
                response.raise_for_status()
                platform = next(
                    (
                        p
                        for p in response.json()["data"]["platforms"]
                        if p.get("id") == "companion-weixin"
                    ),
                    None,
                )
                if platform:
                    wx = platform.get("weixin_oc") or {}
                    sample["channel"] = {
                        "adapter_running": platform.get("status") == "running",
                        "configured": bool(wx.get("configured")),
                        "has_reported_error": bool(
                            platform.get("last_error") or wx.get("last_error")
                        ),
                    }
    except Exception as exc:
        sample["error_type"] = type(exc).__name__
    return sample


class RecoveryPolicy:
    def __init__(self, state=None):
        self.state = state or {"failures": 0, "restart_times": []}

    def decide(self, sample: dict, current: datetime, stopped: bool) -> str:
        if stopped:
            self.state["failures"] = 0
            return "stopped_by_user"
        if sample["process_alive"] and sample["dashboard_http_ok"]:
            self.state["failures"] = 0
            return "healthy"
        self.state["failures"] += 1
        if sample["process_alive"] and self.state["failures"] < 3:
            return "wait_for_local_health"
        recent = [
            t
            for t in self.state["restart_times"]
            if datetime.fromisoformat(t) > current - timedelta(minutes=15)
        ]
        self.state["restart_times"] = recent
        if len(recent) >= 3:
            return "restart_backoff"
        self.state["restart_times"].append(current.isoformat())
        self.state["failures"] = 0
        return "restart_owned" if sample["process_alive"] else "start"


def recover(action: str) -> dict:
    script = (
        PROJECT_ROOT
        / "scripts"
        / ("recover_owned.ps1" if action == "restart_owned" else "start.ps1")
    )
    if (PLUGIN_DATA / "STOP").exists():
        return {"operation": "stopped_before_recovery"}
    started = time.monotonic()
    try:
        log_root = DATA / "companion_logs"
        log_root.mkdir(exist_ok=True)
        launch_log = log_root / (
            "recovery-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            + ".local.log"
        )
        # Captured pipes may remain open in detached grandchildren. A regular
        # private file lets the controller wait for the launcher only.
        with launch_log.open("wb") as stream:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(script),
                ],
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=stream,
                timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        return {
            "operation": action,
            "seconds": round(time.monotonic() - started, 3),
            "exit_code": completed.returncode,
        }
    except Exception as exc:
        return {
            "operation": action,
            "seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
        }


def run_once(
    policy: RecoveryPolicy, *, allow_recovery: bool, state_path: Path | None = None
) -> dict:
    sample = observe()
    action = policy.decide(
        sample, datetime.now(timezone.utc), (PLUGIN_DATA / "STOP").exists()
    )
    sample["decision"] = action
    if allow_recovery and action in ("start", "restart_owned"):
        if state_path is not None:
            # Persist before launch, so a controller crash cannot reset its budget.
            save_state(state_path, policy.state)
        sample["recovery"] = recover(action)
        sample["after_recovery"] = observe()
    return sample


def prune_owned_logs(folder: Path, current: datetime, *, kind: str) -> int:
    patterns = {
        "availability": r"availability-(\d{4}-\d{2}-\d{2})\.local\.jsonl",
        "startup": r"(?:stdout|stderr|watchdog-stdout|watchdog-stderr)-(\d{8})T\d{9}-[0-9a-f]{32}\.local\.log",
    }
    pattern = re.compile(patterns[kind])
    cutoff = current.date() - timedelta(days=8)
    removed = 0
    if not folder.exists():
        return 0
    root = folder.resolve()
    for candidate in folder.iterdir():
        match = pattern.fullmatch(candidate.name)
        if (
            not match
            or candidate.is_symlink()
            or not candidate.is_file()
            or candidate.resolve().parent != root
        ):
            continue
        try:
            day = datetime.strptime(
                match[1], "%Y-%m-%d" if kind == "availability" else "%Y%m%d"
            ).date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                # An owned launcher may still have its historical log open.
                continue
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once",
        action="store_true",
        help="Inspect without restarting or changing the saved recovery budget",
    )
    args = parser.parse_args()
    state = PLUGIN_DATA / "watchdog-state.local.json"
    if args.once:
        print(
            json.dumps(
                run_once(
                    RecoveryPolicy(read_json(state) if state.exists() else None),
                    allow_recovery=False,
                ),
                ensure_ascii=False,
            )
        )
        return
    folder = PLUGIN_DATA / "observations"
    folder.mkdir(exist_ok=True)
    lock = FileLock(str(PLUGIN_DATA / "watchdog.local.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if args.once:
            print("Existing runtime watchdog owns the local lock")
        return
    try:
        policy = RecoveryPolicy(read_json(state) if state.exists() else None)
        (PLUGIN_DATA / "watchdog-process.local.json").write_text(
            json.dumps(
                {"id": os.getpid(), "started_ticks": process_start_ticks(os.getpid())}
            ),
            encoding="utf-8",
        )
        next_sample = time.monotonic()
        pruned_day = None
        while True:
            result = run_once(policy, allow_recovery=not args.once, state_path=state)
            if args.once:
                print(json.dumps(result, ensure_ascii=False))
                return
            now = datetime.now(timezone.utc)
            if now.date() != pruned_day:
                prune_owned_logs(folder, now, kind="availability")
                prune_owned_logs(DATA / "companion_logs", now, kind="startup")
                pruned_day = now.date()
            output = folder / f"availability-{now.date().isoformat()}.local.jsonl"
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            save_state(state, policy.state)
            next_sample = max(next_sample + 30, time.monotonic())
            time.sleep(max(0, next_sample - time.monotonic()))
    finally:
        lock.release()


if __name__ == "__main__":
    main()
