#!/usr/bin/env python3
"""Check initialize capability markers in two disposable candidate processes."""

from __future__ import annotations

import argparse
from contextlib import closing, nullcontext
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


CAPABILITY = "steward-cas-child-prefix-v1"
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_INITIALIZE_LINES = 64
DEADLINE_SECONDS = 30


def validate_smoke(payload: Any, expected_patch_sha256: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_patch_sha256) is None:
        raise ValueError("Initialize smoke requires the exact expected patch SHA-256")
    if not isinstance(payload, dict) or payload.get("capability") != CAPABILITY:
        raise ValueError("Initialize smoke did not identify the required native capability")
    if payload.get("patch_sha256") != expected_patch_sha256:
        raise ValueError("Initialize smoke did not attest the expected source patch")
    if payload.get("enabled_marker_verified") is not True or payload.get("disabled_marker_absent") is not True:
        raise ValueError("Initialize smoke did not verify both enabled and disabled marker behavior")
    if payload.get("scope") != "isolated_candidate_process_only" or payload.get("current_user_endpoint_attested") is not False:
        raise ValueError("Initialize smoke must explicitly retain its isolated candidate scope")
    return payload


def validate_initialize_response(message: Any, enabled: bool, expected_patch_sha256: str) -> str:
    if not isinstance(message, dict) or message.get("id") != 1 or "error" in message:
        raise ValueError("Candidate did not return a successful initialize response")
    result = message.get("result")
    agent = result.get("userAgent") if isinstance(result, dict) else None
    if not isinstance(agent, str):
        raise ValueError("Candidate initialize response has no userAgent")
    marker = f"{CAPABILITY};scope=legacy,paginated;enabled=1;steward-patch={expected_patch_sha256}"
    if enabled and marker not in agent:
        raise ValueError("CAS-enabled candidate did not advertise its enabled storage capability")
    if not enabled and CAPABILITY in agent:
        raise ValueError("CAS-disabled candidate still advertised the storage capability")
    return agent


def _read_initialize(stream, result: queue.Queue) -> None:
    try:
        for _ in range(MAX_INITIALIZE_LINES):
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                raise ValueError("Candidate exited before initialize completed")
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                raise ValueError("Candidate returned an oversized or incomplete protocol line")
            if not raw.strip():
                continue
            message = json.loads(raw)
            if isinstance(message, dict) and message.get("id") == 1:
                result.put(message)
                return
        raise ValueError("Candidate exceeded the bounded initialize message budget")
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        result.put(error)


def _stop(process: subprocess.Popen) -> None:
    if process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _initialize(binary: Path, enabled: bool, deadline: float, expected_patch_sha256: str,
                profile: Path | None = None) -> str:
    storage = nullcontext(str(profile)) if profile is not None else tempfile.TemporaryDirectory(
        prefix="steward-native-initialize-"
    )
    with storage as directory:
        home = Path(directory)
        (home / "config.toml").write_text("[features]\nplugins = false\n", encoding="utf-8")
        environment = os.environ.copy()
        for key in (
            "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_ACCESS_TOKEN", "CHATGPT_TOKEN",
            "CODEX_TOKEN", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_AD_TOKEN",
        ):
            environment.pop(key, None)
        environment["CODEX_HOME"] = str(home)
        if enabled:
            environment["CODEX_STEWARD_CAS"] = "1"
        else:
            environment.pop("CODEX_STEWARD_CAS", None)
        process = subprocess.Popen(
            [str(binary), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=binary.parent, env=environment,
        )
        reader = None
        try:
            assert process.stdin is not None and process.stdout is not None
            incoming: queue.Queue = queue.Queue(maxsize=1)
            reader = threading.Thread(target=_read_initialize, args=(process.stdout, incoming), daemon=True)
            reader.start()
            request = {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex_steward_native_smoke", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
            process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            process.stdin.flush()
            try:
                response = incoming.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as error:
                raise ValueError("Candidate initialize exceeded the 30-second smoke deadline") from error
            if isinstance(response, Exception):
                raise ValueError(f"Candidate initialize failed: {response}") from response
            agent = validate_initialize_response(response, enabled, expected_patch_sha256)
            process.stdin.write(b'{"method":"initialized"}\n')
            process.stdin.flush()
            return agent
        finally:
            _stop(process)
            if reader is not None:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def smoke(binary: Path, expected_patch_sha256: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_patch_sha256) is None:
        raise ValueError("Smoke requires a pinned source patch SHA-256")
    binary = Path(binary)
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("Smoke candidate must be a regular executable file")
    binary = binary.resolve(strict=True)
    deadline = time.monotonic() + DEADLINE_SECONDS
    _initialize(binary, True, deadline, expected_patch_sha256)
    if time.monotonic() >= deadline:
        raise ValueError("Candidate initialize exceeded the 30-second smoke deadline")
    _initialize(binary, False, deadline, expected_patch_sha256)
    return validate_smoke({
        "capability": CAPABILITY,
        "patch_sha256": expected_patch_sha256,
        "enabled_marker_verified": True,
        "disabled_marker_absent": True,
        "scope": "isolated_candidate_process_only",
        "current_user_endpoint_attested": False,
    }, expected_patch_sha256)


def _migration_stamps(profile: Path) -> dict[str, list[tuple]]:
    stamps = {}
    for database in sorted(profile.glob("*.sqlite")):
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='_sqlx_migrations'"
            ).fetchone():
                stamps[database.name] = connection.execute(
                    "SELECT version, success, checksum FROM _sqlx_migrations ORDER BY version"
                ).fetchall()
    return stamps


def verify_stock_profile(binary: Path, stock: Path, patch_sha256: str) -> dict[str, Any]:
    fixture_path = Path(__file__).resolve().parent.parent / "smoke_storage.py"
    fixture_spec = importlib.util.spec_from_file_location("storage_smoke", fixture_path)
    if fixture_spec is None or fixture_spec.loader is None:
        raise ValueError("Packaged Codex profile fixture is unavailable")
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    with tempfile.TemporaryDirectory(prefix="steward-stock-profile-") as directory:
        profile = Path(directory)
        # A real paginated thread also causes stock Codex to initialize the
        # thread-history store. Use synthetic turns from the loopback fixture.
        with ThreadingHTTPServer(("127.0.0.1", 0), fixture.Model) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                fixture.run(stock, profile, server.server_port, False, "paginated")
            finally:
                server.shutdown()
                worker.join()
        before = _migration_stamps(profile)
        expected = {"state_5.sqlite", "logs_2.sqlite", "goals_1.sqlite", "queue_1.sqlite",
                    "memories_1.sqlite", "thread_history_1.sqlite"}
        if not expected.issubset(before) or any(not rows for rows in before.values()):
            raise ValueError("Packaged Codex did not initialize all six Windows databases")
        try:
            _initialize(binary, True, time.monotonic() + DEADLINE_SECONDS, patch_sha256, profile)
        except (OSError, ValueError) as error:
            raise ValueError(f"Candidate could not open a profile initialized by packaged Codex: {error}") from error
        if _migration_stamps(profile) != before:
            raise ValueError("Candidate changed migration records in the packaged Codex profile")
        return {
            "stock_windows_migrations_verified": True,
            "stock_databases": {name: len(rows) for name, rows in before.items()},
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--patch-sha256", required=True)
    parser.add_argument("--stock-binary", type=Path)
    args = parser.parse_args(argv)
    try:
        result = smoke(args.binary, args.patch_sha256)
        if args.stock_binary:
            result.update(verify_stock_profile(
                args.binary.resolve(strict=True), args.stock_binary.resolve(strict=True), args.patch_sha256,
            ))
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "current_user_endpoint_attested": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
