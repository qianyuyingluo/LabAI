"""Opt-in live model smoke test against a running local LabAI backend.

Run: .venv/Scripts/python examples/verify_workspace_agent.py --live
Creates a separate disposable project; never edits the user's existing projects.
The configured model service is called, so its normal token charges apply.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def request(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Explicitly call the configured model")
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run the real model smoke test")
    parent = Path(__file__).resolve().parents[1] / "app" / "storage" / "workspace-verification"
    parent.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="project-", dir=parent))
    (project / "total.py").write_text("def total(values):\n    return sum(values) + 1\n", encoding="utf-8")
    test_source = "from total import total\nassert total([1, 2, 3]) == 6\nassert total([]) == 0\nassert total([-2, 5]) == 3\nprint('WORKSPACE_AGENT_VERIFIED')\n"
    (project / "verify.py").write_text(test_source, encoding="utf-8")
    (project / "AGENTS.md").write_text("Only change total.py. Do not modify verify.py or this file. Run verify.py to validate the fix.\n", encoding="utf-8")
    run = request(args.backend, "/api/runs/chat", {
        "message": "工作区 Agent 验收：请检查本目录的规则和代码，修复 total.py 的错误。只修改 total.py，不修改测试。"
                   f"请实际执行 verify.py 验证，Python 解释器路径是 {sys.executable}。最后用中文报告修改与验证结果。",
        "workspace_path": str(project), "workspace_mode": "auto",
    })
    message_id = run["assistant_message"]["id"]
    print(json.dumps({"chat_id": run["chat_id"], "message_id": message_id, "workspace": str(project)}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + 240
    while True:
        chat = request(args.backend, "/api/chats/" + run["chat_id"])
        message = next(m for m in chat["messages"] if m["id"] == message_id)
        if message["status"] != "streaming":
            break
        if time.monotonic() > deadline:
            print("Observation deadline reached; the run remains live. Inspect the same message rather than restarting.", flush=True)
            return 2
        time.sleep(2)
    calls = (message.get("metadata") or {}).get("workspaceAgent", {}).get("calls", [])
    # Independent verification with a fixed test source, not the model's claims.
    unchanged = (project / "verify.py").read_text(encoding="utf-8") == test_source
    result = subprocess.run([sys.executable, "-B", "verify.py"], cwd=project, capture_output=True, text=True, timeout=15)
    checks = {"message_done": message["status"] == "done", "test_unchanged": unchanged,
              "independent_test_passed": result.returncode == 0 and "WORKSPACE_AGENT_VERIFIED" in result.stdout,
              "agent_edited": any(c["tool"] in {"workspace_edit", "workspace_write"} and c["status"] == "done" for c in calls),
              "agent_ran_successful_command": any(c["tool"] == "run_command" and c.get("result", {}).get("exit_code") == 0 for c in calls)}
    receipt = {"chat_id": run["chat_id"], "message_id": message_id, "workspace": str(project), "checks": checks,
               "tools": [{"tool": c["tool"], "status": c["status"]} for c in calls],
               "independent_stdout": result.stdout, "independent_stderr": result.stderr,
               "total_sha256": hashlib.sha256((project / "total.py").read_bytes()).hexdigest(), "answer": message["content"]}
    receipt_path = project.parent / (project.name + "-receipt.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "receipt": str(receipt_path)}, ensure_ascii=False), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
