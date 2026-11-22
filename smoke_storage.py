"""Exercise real persisted child and manual fork paths against a loopback model."""

import argparse
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
from uuid import uuid4


PREFIX = "Storage fixture: " + "retain this synthetic inherited text. " * 512
CHILD_TASKS = ("Return fixture child A.", "Return fixture child B.")


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        response_id = uuid4().hex
        events = [{"type": "response.created", "response": {"id": response_id}}]
        context = request.get("input", [])
        spawn_tools = []
        for tool in request.get("tools", []):
            if tool.get("name", "").endswith("spawn_agent"):
                spawn_tools.append((tool["name"], None))
            for child_tool in tool.get("tools", []):
                if child_tool.get("name", "").endswith("spawn_agent"):
                    spawn_tools.append((child_tool["name"], tool["name"]))
        last_user = next((item for item in reversed(context) if item.get("role") == "user"), {})
        if (spawn_tools and "Create the fixture children." in json.dumps(last_user)
                and not any(str(item.get("call_id", "")).startswith("fixture-call-") for item in context)):
            name, namespace = spawn_tools[0]
            for index, task in enumerate(CHILD_TASKS):
                events.append({
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": f"fixture-call-{index}",
                        "name": name,
                        **({"namespace": namespace} if namespace else {}),
                        "arguments": json.dumps({"message": task, "fork_context": True}),
                    },
                })
        else:
            if "Create the fixture children." in json.dumps(last_user) and not spawn_tools:
                print("Root tool names:", [(tool.get("type"), tool.get("name"))
                                          for tool in request.get("tools", [])], flush=True)
            events.append({
                "type": "response.output_item.done",
                "item": {
                    "type": "message", "role": "assistant", "id": f"msg-{response_id}",
                    "content": [{"type": "output_text", "text": "Fixture complete."}],
                },
            })
        events.append({
            "type": "response.completed",
            "response": {"id": response_id, "usage": {"input_tokens": 1,
                         "output_tokens": 1, "total_tokens": 2}},
        })
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def write_config(home, port):
    (home / "config.toml").write_text(f'''
model = "gpt-5"
model_provider = "fixture"
approval_policy = "never"
analytics.enabled = false
[features]
plugins = false
multi_agent = true
multi_agent_v2 = false
[model_providers.fixture]
name = "Loopback fixture"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
''', encoding="utf-8")


def run(binary, home, port, patched, mode, server_command=None):
    if server_command is None:
        write_config(home, port)
        server_command = [str(binary), "app-server"]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("OPENAI_", "CODEX_", "CHATGPT_", "AZURE_OPENAI_"))}
    env.update(CODEX_HOME=str(home), CODEX_STEWARD_CAS="1")
    with (home / "stderr.log").open("w+", encoding="utf-8") as stderr, subprocess.Popen(
        server_command, cwd=home, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=stderr, encoding="utf-8",
    ) as process:
        received = Queue()

        def read_messages():
            for line in process.stdout:
                if line.strip():
                    received.put(json.loads(line))
            received.put(None)

        Thread(target=read_messages, daemon=True).start()
        messages = []

        def expect(predicate):
            deadline = time.monotonic() + 45
            while not any(predicate(message) for message in messages):
                message = received.get(timeout=max(0.001, deadline - time.monotonic()))
                if message is None:
                    raise RuntimeError("app-server exited")
                messages.append(message)
            return next(message for message in messages if predicate(message))

        def rpc(method, params, request_id=None):
            json_request = {"method": method, "params": params}
            if request_id is not None:
                json_request["id"] = request_id
            process.stdin.write(json.dumps(json_request) + "\n")
            process.stdin.flush()
            if request_id is not None:
                reply = expect(lambda message: message.get("id") == request_id)
                if "error" in reply:
                    raise AssertionError(reply["error"])
                return reply["result"]

        def turn(text, request_id):
            result = rpc("turn/start", {"threadId": parent_id,
                         "input": [{"type": "text", "text": text}]}, request_id)
            turn_id = result["turn"]["id"]
            completed = expect(lambda message: message.get("method") == "turn/completed"
                               and message.get("params", {}).get("turn", {}).get("id") == turn_id)
            assert completed["params"]["turn"]["status"] == "completed", completed

        try:
            info = rpc("initialize", {"clientInfo": {"name": "childfix_fixture", "version": "1.0"},
                                      "capabilities": {"experimentalApi": True}}, 1)
            if patched:
                assert "steward-cas-child-prefix-v1;scope=legacy,paginated;enabled=1" in info["userAgent"]
            rpc("initialized", {})
            parent_id = rpc("thread/start", {"cwd": str(home), "historyMode": mode}, 2)["thread"]["id"]
            turn(PREFIX, 3)
            turn("Create the fixture children.", 4)
            for _ in CHILD_TASKS:
                completion = expect(lambda message: message.get("method") == "turn/completed"
                                    and message["params"]["threadId"] != parent_id)
                messages.remove(completion)
            fork = rpc("thread/fork", {"threadId": parent_id, "excludeTurns": True}, 5)["thread"]
            fork_id = fork["id"]
        except BaseException:
            stderr.seek(0)
            print(stderr.read()[-1200:])
            print([message.get("method", message.get("id")) for message in messages[-24:]])
            raise
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    paths = sorted((home / "sessions").rglob("rollout-*.jsonl"))
    parent_path = next(path for path in paths if parent_id in path.name)
    assert PREFIX in parent_path.read_text(encoding="utf-8")
    fork_path = next(path for path in paths if fork_id in path.name)
    fork_text = fork_path.read_text(encoding="utf-8")
    fork_meta = json.loads(fork_text.splitlines()[0])["payload"]
    assert bool(fork_meta.get("history_base")) == (mode == "paginated")
    assert (PREFIX in fork_text) == (mode == "legacy")
    siblings = [path for path in paths if path not in (parent_path, fork_path)]
    assert len(siblings) == 2, [path.name for path in paths]
    if patched:
        manifests = home / "steward-cas-v1" / "prefixes"
        shared = []
        for child in siblings:
            header = json.loads(child.read_text(encoding="utf-8").splitlines()[0])
            assert header["steward_cas_v1"]["prefix_end_ordinal"] > 1
            prefix_lines = (manifests / (child.name + ".prefix")).read_text(encoding="utf-8")
            referenced = {json.loads(line)["sha256"] for line in prefix_lines.splitlines()}
            export = home / f"{child.stem}-plain.jsonl"
            subprocess.run([str(binary), "--steward-export-rollout", str(child), str(export)],
                           cwd=home, env=env, check=True, capture_output=True, timeout=20)
            actual = set()
            for line in export.read_bytes().splitlines(keepends=True):
                split = line.find(b',"type":')
                if PREFIX.encode() in line and split >= 0:
                    actual.add(sha256(line[split:]).hexdigest())
            shared.append(referenced & actual)
        common = shared[0] & shared[1]
        assert common, "Siblings do not reference the same inherited fixture bytes"
        assert all((home / "steward-cas-v1" / "blobs" / f"{digest}.zst").is_file()
                   for digest in common)
    print(f"{mode}: two real sub-agent rollouts, manual fork, CAS={patched} checks passed")
    return {"parent": parent_id, "fork": fork_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--patched", action="store_true")
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", 0), Model) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        for mode in ("legacy", "paginated"):
            with TemporaryDirectory(prefix="childfix-fixture-") as tmp:
                run(args.binary.resolve(strict=True), Path(tmp), server.server_port, args.patched, mode)
        server.shutdown()
