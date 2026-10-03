#!/usr/bin/env python3
"""Run Codex for Claude Code and expose live progress as a VS Code log."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def save_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def exclude_local_history(repo: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True, text=True, check=True,
    )
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if "/.codex-handoff/" not in existing.splitlines():
        with exclude.open("a", encoding="utf-8") as file:
            if existing and not existing.endswith("\n"):
                file.write("\n")
            file.write("/.codex-handoff/\n")


def start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    spec = Path(args.spec).resolve()
    if not repo.is_dir() or not spec.is_file():
        raise SystemExit("Repository or spec file does not exist")
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True).returncode:
        raise SystemExit("--repo must be inside a Git repository")
    if not shutil.which("codex"):
        raise SystemExit("Codex CLI is not installed or is not on PATH")
    if args.model == "gpt-6-luna" and args.reasoning_effort == "ultra":
        raise SystemExit("gpt-6-luna does not support ultra reasoning effort")
    child_model = args.subagent_model or args.model
    if child_model == "gpt-6-luna" and args.subagent_reasoning_effort == "ultra":
        raise SystemExit("gpt-6-luna subagents do not support ultra reasoning effort")

    history = repo / ".codex-handoff"
    runs = history / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    exclude_local_history(repo)
    run_dir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=runs))
    run_dir.chmod(0o700)
    saved_spec = run_dir / "spec.md"
    saved_spec.write_bytes(spec.read_bytes())
    sandbox_mode = "danger-full-access" if args.full_access else "workspace-write"
    status = {"state": "running", "repo": str(repo), "model": args.model,
              "reasoning_effort": args.reasoning_effort,
              "subagent_model": args.subagent_model,
              "subagent_reasoning_effort": args.subagent_reasoning_effort,
              "sandbox_mode": sandbox_mode,
              "run_dir": str(run_dir), "thread_id": args.resume,
              "progress_log": str(run_dir / "progress.log"),
              "raw_log": str(run_dir / "events.jsonl"),
              "final_response": str(run_dir / "final-response.md"),
              "transcript_md": str(run_dir / "transcript.md"),
              "transcript_uri": (run_dir / "transcript.md").as_uri(),
              "transcript_relative": "./" + str((run_dir / "transcript.md").relative_to(repo)),
              "index_md": str(history / "index.md")}
    (run_dir / "progress.log").write_text("Codex is starting...\n", encoding="utf-8")
    (run_dir / "transcript.md").write_text(
        "# Codex session\n\n**Repository:** `" + str(repo) + "`  \n"
        "**Model:** `" + args.model + "`  \n"
        "**Reasoning effort:** `" + str(args.reasoning_effort or "model default") + "`  \n"
        "**Spawned subagent model:** `" + str(args.subagent_model or "inherit") + "`  \n"
        "**Spawned subagent effort:** `" + str(args.subagent_reasoning_effort or "inherit") + "`  \n"
        "**Sandbox:** `" + sandbox_mode + "`  \n"
        "**Status:** running\n\n## Task specification\n\n"
        + spec.read_text(encoding="utf-8") + "\n\n## Activity\n\n",
        encoding="utf-8",
    )
    title = next((line.strip().lstrip("# ") for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()), "Codex task")[:100]
    index = history / "index.md"
    if not index.exists():
        index.write_text("# Codex handoff history\n\n", encoding="utf-8")
    with index.open("a", encoding="utf-8") as file:
        file.write("- [" + time.strftime("%Y-%m-%d %H:%M:%S") + " — " + title.replace("]", "\\]")
                   + "](" + str((run_dir / "transcript.md").relative_to(history)) + ")\n")
    save_json(run_dir / "status.json", status)
    with (run_dir / "worker.stderr.log").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "worker", "--run", str(run_dir)],
            cwd=repo, stdin=subprocess.DEVNULL, stdout=err, stderr=err,
            start_new_session=True,
        )
    (run_dir / "worker.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    if not os.environ.get("CODEX_HANDOFF_NO_EDITOR") and shutil.which("code"):
        subprocess.Popen(["code", "--reuse-window", str(run_dir / "transcript.md")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    print(json.dumps(status, ensure_ascii=False))
    return 0


def item_summary(event: dict) -> str | None:
    kind = event.get("type", "")
    item = event.get("item") or {}
    item_type = item.get("type", "")
    if kind == "thread.started":
        return f"Codex thread: {event.get('thread_id', '?')}"
    if kind in ("turn.started", "turn.completed", "turn.failed", "error"):
        return kind + (": " + str(event.get("message")) if event.get("message") else "")
    if kind == "item.started" and item_type == "command_execution":
        return "Running: " + str(item.get("command", ""))
    if kind == "item.completed" and item_type == "command_execution":
        return "Command finished: " + str(item.get("exit_code", item.get("status", "")))
    if kind == "item.completed" and item_type == "agent_message":
        return "Codex: " + str(item.get("text", ""))
    if kind == "item.completed" and item_type == "file_change":
        return "File change: " + json.dumps(item.get("changes", []), ensure_ascii=False)
    return None


def transcript_entry(event: dict) -> str | None:
    kind = event.get("type", "")
    item = event.get("item") or {}
    item_type = item.get("type", "")
    if kind == "thread.started":
        return "**Codex thread:** `" + str(event.get("thread_id", "?")) + "`\n\n"
    if kind == "item.started" and item_type == "command_execution":
        return "### Command\n\n```sh\n" + str(item.get("command", "")) + "\n```\n\n"
    if kind == "item.completed" and item_type == "command_execution":
        return "Command finished: `" + str(item.get("exit_code", item.get("status", "?"))) + "`\n\n"
    if kind == "item.completed" and item_type == "agent_message":
        return "### Codex\n\n" + str(item.get("text", "")) + "\n\n"
    if kind == "item.completed" and item_type == "file_change":
        return "### File changes\n\n```json\n" + json.dumps(item.get("changes", []), ensure_ascii=False, indent=2) + "\n```\n\n"
    if kind in ("turn.started", "turn.completed", "turn.failed", "error"):
        return "**" + kind + "**\n\n"
    return None


def effort_overrides(status: dict) -> list[str]:
    options: list[str] = []
    for key, value in (
        ("model_reasoning_effort", status.get("reasoning_effort")),
        ("agents.default_subagent_model", status.get("subagent_model")),
        ("agents.default_subagent_reasoning_effort", status.get("subagent_reasoning_effort")),
    ):
        if value:
            options.extend(["-c", f'{key}="{value}"'])
    return options


def worker(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    status_file = run_dir / "status.json"
    status = read_json(status_file)
    repo = Path(status["repo"])
    spec = (run_dir / "spec.md").read_text(encoding="utf-8")
    thread_id = status.get("thread_id")
    full_access = status.get("sandbox_mode") == "danger-full-access"
    if thread_id:
        command = ["codex", "exec", "resume", "--json", "-m", status["model"]]
        command.extend(effort_overrides(status))
        if full_access:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["-c", 'sandbox_mode="workspace-write"'])
        command.extend([thread_id, "-"])
    else:
        command = ["codex", "exec", "-C", str(repo), "-m", status["model"], "--json"]
        command.extend(effort_overrides(status))
        if full_access:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["-s", "workspace-write"])
        command.append("-")
    final = ""
    try:
        with (run_dir / "events.jsonl").open("w", encoding="utf-8") as raw, \
             (run_dir / "progress.log").open("a", encoding="utf-8") as progress, \
             (run_dir / "transcript.md").open("a", encoding="utf-8") as transcript:
            progress.write("Model: " + status["model"]
                           + "\nReasoning effort: " + str(status.get("reasoning_effort") or "model default")
                           + "\nSpawned subagent effort: " + str(status.get("subagent_reasoning_effort") or "inherit")
                           + "\nRepository: " + str(repo)
                           + "\nSandbox: " + status.get("sandbox_mode", "workspace-write") + "\n\n")
            progress.flush()
            proc = subprocess.Popen(command, cwd=repo, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            assert proc.stdin and proc.stdout
            proc.stdin.write(spec)
            proc.stdin.close()
            for line in proc.stdout:
                raw.write(line)
                raw.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    progress.write(line)
                    progress.flush()
                    transcript.write("```text\n" + line.rstrip("\n") + "\n```\n\n")
                    transcript.flush()
                    continue
                if event.get("type") == "thread.started":
                    thread_id = event.get("thread_id", thread_id)
                    status["thread_id"] = thread_id
                    save_json(status_file, status)
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                    final = str(item.get("text", ""))
                summary = item_summary(event)
                if summary:
                    progress.write(summary + "\n")
                    progress.flush()
                entry = transcript_entry(event)
                if entry:
                    transcript.write(entry)
                    transcript.flush()
            code = proc.wait()
            progress.write(f"\nCodex exited with code {code}.\n")
            transcript.write("\n**Codex exit code:** `" + str(code) + "`\n")
        (run_dir / "final-response.md").write_text(final, encoding="utf-8")
        status.update(state="completed" if code == 0 else "failed", exit_code=code,
                      thread_id=thread_id)
        save_json(status_file, status)
        return code
    except Exception as exc:
        status.update(state="failed", error=str(exc))
        save_json(status_file, status)
        with (run_dir / "progress.log").open("a", encoding="utf-8") as progress:
            progress.write("\nBridge error: " + str(exc) + "\n")
        with (run_dir / "transcript.md").open("a", encoding="utf-8") as transcript:
            transcript.write("\n**Bridge error:** " + str(exc) + "\n")
        return 1


def report(args: argparse.Namespace, wait: bool) -> int:
    run_dir = Path(args.run).resolve()
    status_file = run_dir / "status.json"
    if not status_file.is_file():
        raise SystemExit("No Codex run found at " + str(run_dir))
    deadline = time.monotonic() + 55 if wait else time.monotonic()
    while True:
        status = read_json(status_file)
        if status["state"] != "running" or time.monotonic() >= deadline:
            print(json.dumps(status, ensure_ascii=False))
            return 0 if status["state"] != "failed" else 1
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--repo", required=True)
    start_parser.add_argument("--spec", required=True)
    start_parser.add_argument("--model", default="gpt-6.1-sol",
                              choices=["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"])
    efforts = ["low", "medium", "high", "xhigh", "max", "ultra"]
    start_parser.add_argument("--reasoning-effort", choices=efforts,
                              help="Reasoning effort for the Codex implementer; omit for model default")
    start_parser.add_argument("--subagent-model", choices=["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"],
                              help="Default model if this Codex run spawns its own subagents")
    start_parser.add_argument("--subagent-reasoning-effort", choices=efforts,
                              help="Default effort if this Codex run spawns its own subagents")
    start_parser.add_argument("--resume")
    start_parser.add_argument("--full-access", action="store_true",
                              help="Run Codex without its sandbox or approval prompts for this invocation")
    for name in ("status", "wait", "worker"):
        sub.add_parser(name).add_argument("--run", required=True)
    args = parser.parse_args()
    if args.action == "start":
        return start(args)
    if args.action == "worker":
        return worker(args)
    return report(args, args.action == "wait")


if __name__ == "__main__":
    sys.exit(main())
