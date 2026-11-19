"""Measure whether the code map helps a coding agent on your repo.

Each task is run several times in two ways:

    plain   the task as written
    graph   the task plus the ranked file list from the code map

Every run happens in a throwaway git worktree at one fixed commit, so your working
copy is never touched and every run starts from the same code. The agent is Claude
Code in non-interactive mode; its event stream gives tool calls, tokens, cost and
timings.
"""
import json
import os
import random
import shlex
import statistics
import subprocess
import tempfile
import threading
import time

from . import graph as G
from .retrieve import Index
from .scan import scan

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEFAULT_AGENT = ("claude -p --output-format stream-json --verbose --permission-mode acceptEdits "
                 "--allowedTools Read,Grep,Glob,Edit,Write --max-turns 40")
METRICS = [("tool_calls", "Tool calls", "{:.0f}"), ("tokens", "Tokens", "{:,.0f}"),
           ("first_edit_s", "Time to first edit (s)", "{:.0f}"), ("duration_s", "Time on task (s)", "{:.0f}"),
           ("cost_usd", "Cost (USD)", "{:.2f}")]


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=check)


class Worktree:
    """A clean checkout of one commit in a temp folder, removed afterwards."""

    def __init__(self, repo, commit):
        self.repo, self.commit = repo, commit

    def __enter__(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="atl-bench-"), "repo")
        git(self.repo, "worktree", "add", "--detach", self.path, self.commit)
        return self.path

    def __exit__(self, *exc):
        git(self.repo, "worktree", "remove", "--force", self.path, check=False)
        git(self.repo, "worktree", "prune", check=False)


def hint_for(ix, question, k):
    """The text added to the task in the graph arm."""
    res = ix.graph_search(question, k)
    if not res["files"]:
        return "", []
    verbs = ["in", "import", "call", "API call", "changed together"]
    lines, files = [], []
    for rank, (f, _) in enumerate(res["files"], 1):
        path = ix.nodes[f]["path"]
        files.append(path)
        steps = ix.chain(res["anchors"], f)
        why = ""
        if len(steps) > 1:
            why = "  (" + "".join((f" ~{verbs[kind]}~ " if kind is not None else "") + ix.nodes[i]["l"]
                                   for i, kind in steps) + ")"
        lines.append(f"{rank}. {path}{why}")
    text = ("\n\nA code map of this repo ranks these files as the most relevant to the task. "
            "Start with them, and check them yourself before relying on the list:\n" + "\n".join(lines))
    return text, files


def run_agent(agent_cmd, prompt, cwd, timeout, log_path):
    """Run the agent once and read its event stream. Returns the measurements."""
    start = time.time()
    m = {"tool_calls": 0, "first_edit_s": None, "edited": [], "tokens": 0, "cost_usd": None,
         "turns": None, "is_error": False, "duration_s": None, "note": ""}
    proc = subprocess.Popen(shlex.split(agent_cmd) + [prompt], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    usage_seen = {}
    timed_out = []
    timer = threading.Timer(timeout, lambda: (timed_out.append(1), proc.kill()))
    timer.start()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "assistant":
                    msg = ev.get("message", {})
                    if msg.get("id") and msg.get("usage"):
                        usage_seen[msg["id"]] = msg["usage"]
                    for block in msg.get("content", []) or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            m["tool_calls"] += 1
                            if block.get("name") in EDIT_TOOLS:
                                if m["first_edit_s"] is None:
                                    m["first_edit_s"] = round(time.time() - start, 1)
                                path = (block.get("input") or {}).get("file_path")
                                if path:
                                    m["edited"].append(path)
                elif ev.get("type") == "result":
                    m["cost_usd"] = ev.get("total_cost_usd")
                    m["turns"] = ev.get("num_turns")
                    m["is_error"] = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
                    if ev.get("usage"):
                        usage_seen = {"result": ev["usage"]}
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        timer.cancel()
    if timed_out:
        m["is_error"], m["note"] = True, "timed out"
    err = (proc.stderr.read() or "").strip()
    proc.stdout.close()
    proc.stderr.close()
    if proc.returncode not in (0, None) and not m["note"]:
        m["is_error"], m["note"] = True, err[-300:] or f"exit code {proc.returncode}"
    for u in usage_seen.values():
        m["tokens"] += sum(u.get(key) or 0 for key in (
            "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    m["duration_s"] = round(time.time() - start, 1)
    return m


def load_tasks(path):
    with open(path, encoding="utf-8") as f:
        tasks = json.load(f)
    for t in tasks:
        if not t.get("id") or not t.get("prompt"):
            raise SystemExit("Every task needs an \"id\" and a \"prompt\". See sample/bench-tasks.example.json.")
    return tasks


def cmd_run(a):
    repo = os.path.abspath(os.path.expanduser(a.repo))
    if git(repo, "rev-parse", "--git-dir", check=False).returncode != 0:
        raise SystemExit(f"{repo} is not a git repo. The benchmark needs one so every run starts from the same commit.")
    commit = git(repo, "rev-parse", a.commit).stdout.strip()
    tasks = load_tasks(a.tasks)
    out = os.path.join(a.out, "bench")
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    results_path = os.path.join(out, "results.jsonl")
    done = set()
    if os.path.exists(results_path) and not a.fresh:
        with open(results_path, encoding="utf-8") as f:
            done = {(r["task"], r["arm"], r["rep"]) for r in map(json.loads, f) if r["commit"] == commit}
    elif os.path.exists(results_path):
        os.remove(results_path)

    print(f"Repo    {repo}\nCommit  {commit[:10]}\nAgent   {a.agent}")
    print("Building the code map at that commit ...")
    with Worktree(repo, commit) as wt:
        raw = scan(wt, log=lambda *x: None)
        ix = Index(G.pack(raw, G.restructure(raw["nodes"], raw["edges"]), {}))
    plan = []
    rng = random.Random(a.seed)
    for rep in range(1, a.repeats + 1):
        for t in tasks:
            arms = ["plain", "graph"]
            rng.shuffle(arms)  # alternate which arm goes first, so neither always benefits from a warm cache
            plan += [(t, arm, rep) for arm in arms if (t["id"], arm, rep) not in done]
    for t in tasks:
        text, files = hint_for(ix, t.get("question") or t["prompt"], a.k)
        t["_hint"], t["_hint_files"] = text, files
        print(f"\n  {t['id']}: {t['prompt'][:90]}")
        for f in files:
            print(f"      map suggests  {f}")
        if not files:
            print("      the map found nothing for this task; the graph arm will equal the plain arm")
    total = len(tasks) * a.repeats * 2
    print(f"\n{len(plan)} agent runs to do ({total - len(plan)} of {total} already recorded).")
    if a.dry_run or not plan:
        return
    if not a.yes and input("Each run uses your Claude Code account. Start? [y/N] ").strip().lower() != "y":
        return

    for n, (t, arm, rep) in enumerate(plan, 1):
        prompt = t["prompt"] + (t["_hint"] if arm == "graph" else "")
        log_path = os.path.join(out, "logs", f"{t['id']}-{arm}-{rep}.jsonl")
        print(f"[{n}/{len(plan)}] {t['id']}  {arm}  run {rep} ... ", end="", flush=True)
        with Worktree(repo, commit) as wt:
            m = run_agent(a.agent, prompt, wt, a.timeout, log_path)
            status = git(wt, "status", "--porcelain", check=False).stdout
            changed = sorted({line[3:].strip() for line in status.split("\n") if line.strip()})
        want = t.get("expect_files") or []
        row = {"task": t["id"], "arm": arm, "rep": rep, "commit": commit, "changed": changed,
               "hit": (all(any(c.endswith(w) for c in changed) for w in want) if want else None),
               **{k: v for k, v in m.items() if k != "edited"}}
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        flag = "  ERROR " + m["note"] if m["is_error"] else ""
        print(f"{m['tool_calls']} tool calls, {m['tokens']:,} tokens, {m['duration_s']:.0f} s{flag}")
    print()
    cmd_report(a)


def _median(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def summarise(rows):
    """Per task and pooled: the median of each measure in each arm, and the change."""
    good = [r for r in rows if not r.get("is_error")]
    tasks = sorted({r["task"] for r in good})
    per_task, pooled = [], {}
    for t in tasks:
        entry = {"task": t}
        for key, _, _ in METRICS:
            p = _median([r for r in good if r["task"] == t and r["arm"] == "plain"], key)
            g = _median([r for r in good if r["task"] == t and r["arm"] == "graph"], key)
            entry[key] = {"plain": p, "graph": g,
                          "change": (g - p) / p if p and g is not None else None}
        per_task.append(entry)
    for key, _, _ in METRICS:
        changes = [e[key]["change"] for e in per_task if e[key]["change"] is not None]
        pooled[key] = {"median_change": statistics.median(changes) if changes else None,
                       "better": sum(c < 0 for c in changes), "of": len(changes)}
    hits = {arm: [r["hit"] for r in good if r["arm"] == arm and r.get("hit") is not None]
            for arm in ("plain", "graph")}
    return {"per_task": per_task, "pooled": pooled, "runs": len(rows), "failed": len(rows) - len(good),
            "tasks": len(tasks), "hits": {arm: (sum(v), len(v)) for arm, v in hits.items()}}


def cmd_report(a):
    path = os.path.join(a.out, "bench", "results.jsonl")
    if not os.path.exists(path):
        raise SystemExit("No results yet. Run:  python3 run.py bench run --repo <path> --tasks <file>")
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    s = summarise(rows)
    pct = lambda c: "   n/a" if c is None else f"{c:+.0%}"
    print(f"{s['runs']} runs on {s['tasks']} tasks ({s['failed']} failed runs left out). "
          "Negative change = the graph arm used less.\n")
    for key, label, fmt in METRICS:
        print(f"{label}")
        print(f"  {'task':28}{'plain':>12}{'graph':>12}{'change':>9}")
        for e in s["per_task"]:
            v = e[key]
            show = lambda x: "n/a" if x is None else fmt.format(x)
            print(f"  {e['task'][:27]:28}{show(v['plain']):>12}{show(v['graph']):>12}{pct(v['change']):>9}")
        p = s["pooled"][key]
        print(f"  {'median change across tasks':52}{pct(p['median_change']):>9}   "
              f"(graph better on {p['better']} of {p['of']} tasks)\n")
    for arm, (hit, n) in s["hits"].items():
        if n:
            print(f"Runs that changed every expected file, {arm}: {hit} of {n}")
    with open(os.path.join(a.out, "bench", "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    print(f"\nSaved {a.out}/bench/summary.json. Raw agent logs are in {a.out}/bench/logs/.")
    if s["tasks"] < 5 or s["runs"] < s["tasks"] * 6:
        print("Note: with fewer than 5 tasks or 3 runs per arm, treat these as a first look, not a result.")


def add_commands(sub):
    b = sub.add_parser("bench", help="measure the code map's effect on a coding agent")
    b = b.add_subparsers(dest="cmd", required=True)
    r = b.add_parser("run", help="run every task with and without the map")
    r.add_argument("--repo", required=True, help="a git repo to run the tasks in")
    r.add_argument("--tasks", required=True, help="JSON file of tasks (see sample/bench-tasks.example.json)")
    r.add_argument("--repeats", type=int, default=3, help="runs per task per arm (default 3)")
    r.add_argument("--commit", default="HEAD", help="commit every run starts from (default HEAD)")
    r.add_argument("-k", type=int, default=5, help="how many files the map suggests (default 5)")
    r.add_argument("--agent", default=DEFAULT_AGENT, help="agent command; the prompt is added as the last argument")
    r.add_argument("--timeout", type=int, default=900, help="seconds before a run is stopped (default 900)")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--dry-run", action="store_true", help="show the plan and the map's suggestions, run nothing")
    r.add_argument("--fresh", action="store_true", help="discard earlier results instead of resuming")
    r.add_argument("--yes", action="store_true", help="do not ask before starting")
    r.set_defaults(fn=cmd_run)
    p = b.add_parser("report", help="print the comparison from saved results")
    p.set_defaults(fn=cmd_report)
    for sp in (r, p):
        sp.add_argument("--out", default="out", help="where results are kept (default: out)")
