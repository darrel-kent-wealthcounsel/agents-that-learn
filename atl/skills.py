"""Turn repeated failures in agent traces into a reviewed skill update.

    traces  ->  draft  ->  human review  ->  skill box

The draft is written by a small rule-based reader by default, so the demo runs
offline and gives the same result every time. `--live` asks a language model instead.
Nothing becomes active until a person approves it.
"""
import difflib
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.request
from collections import Counter

from .text import query_tokens, tokens

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SKILLS = os.path.join(HERE, "sample", "skills")
SAMPLE_TRACES = os.path.join(HERE, "sample", "traces.jsonl")
CHANGE_TOOLS = {"edit", "write"}

CONTRACT = """You maintain written skills for a coding agent. You are given one skill and several
past runs (traces) where the agent used it. Find a failure that repeats across runs and the
fix that ended it, then rewrite the skill so the agent avoids the failure next time.

Rules:
- Change only what the evidence supports. Keep the skill's format and its other steps.
- Record the error and its fix under a "## Known errors" heading.
- List the ids of the runs you relied on. Use only ids that appear in the input.
- If earlier drafts were rejected, the reasons are included. Do not repeat those mistakes.
- If no failure repeats in at least two runs, return {"body": null}.

Reply with JSON only: {"body": "<full new skill text, without front matter>",
"summary": "<one sentence>", "evidence": ["<run id>", ...]}"""

# ----------------------------------------------------------------- terminal helpers

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def heading(text):
    print("\n" + paint(text, "1"))
    print("-" * len(text))


# ----------------------------------------------------------------- skill files


def parse_skill(text, fallback_name):
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip("\"'")
    try:
        version = int(float(meta.get("version", 1)))
    except ValueError:
        version = 1
    return {"name": meta.get("name", fallback_name), "version": version,
            "description": meta.get("description", ""), "body": body.strip() + "\n"}


def render_skill(skill):
    return (f"---\nname: {skill['name']}\nversion: {skill['version']}\n"
            f"description: {skill['description']}\n---\n\n{skill['body']}")


class Box:
    """The skill box: every version of every skill, which one is active, and the review log."""

    def __init__(self, folder):
        self.folder = folder
        self.state_path = os.path.join(folder, "box.json")
        self.state = {"active": {}, "pending": [], "reviews": [], "traces": SAMPLE_TRACES}
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                self.state = json.load(f)

    def save(self):
        os.makedirs(self.folder, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def seed(self, skills_dir, traces):
        if os.path.isdir(self.folder):
            shutil.rmtree(self.folder)
        self.state = {"active": {}, "pending": [], "reviews": [], "traces": os.path.abspath(traces)}
        count = 0
        for dirpath, _, names in sorted(os.walk(skills_dir)):
            for name in sorted(names):
                if not name.endswith(".md"):
                    continue
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                fallback = os.path.basename(dirpath) if name.upper() == "SKILL.MD" else name[:-3]
                skill = parse_skill(text, fallback)
                self.write(skill)
                self.state["active"][skill["name"]] = skill["version"]
                count += 1
        self.save()
        return count

    def write(self, skill):
        d = os.path.join(self.folder, skill["name"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"v{skill['version']}.md"), "w", encoding="utf-8") as f:
            f.write(render_skill(skill))

    def get(self, name, version=None):
        version = version or self.state["active"][name]
        with open(os.path.join(self.folder, name, f"v{version}.md"), encoding="utf-8") as f:
            return parse_skill(f.read(), name)

    def active(self):
        return [self.get(n) for n in sorted(self.state["active"])]

    def search(self, task, k=1):
        skills = self.active()
        docs = [Counter(tokens(s["name"]) * 3 + tokens(s["description"]) * 2 + tokens(s["body"]))
                for s in skills]
        df = Counter(t for d in docs for t in d)
        scored = []
        for skill, doc in zip(skills, docs):
            score = sum(math.log(1 + len(docs) / df[t]) * doc[t] / (doc[t] + 1.5)
                        for t in query_tokens(task) if t in doc)
            scored.append((-score, skill["name"], skill))
        scored.sort(key=lambda x: x[:2])
        return [s for _, _, s in scored[:k]]


def open_box(a):
    box = Box(os.path.join(a.out, "skillbox"))
    if not os.path.exists(box.state_path):
        box.seed(SAMPLE_SKILLS, SAMPLE_TRACES)
    return box


def load_traces(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ----------------------------------------------------------------- reading the traces


def signature(output):
    """The error line, with file paths and numbers removed so repeats compare equal."""
    lines = [l.strip() for l in (output or "").split("\n") if l.strip()]
    line = next((l for l in lines if "error" in l.lower()), lines[0] if lines else "failed")
    line = re.sub(r"""["']?[\w.\-/]+/[\w.\-]+\.[A-Za-z]\w*["']?""", "<file>", line)
    return re.sub(r"\d+", "N", line)[:160]


def find_lessons(episodes, min_runs=2):
    """Failures that repeat across runs, each with what caused it and what fixed it."""
    groups = {}
    for ep in episodes:
        steps = ep["steps"]
        for i, st in enumerate(steps):
            if st.get("ok", True):
                continue
            later = steps[i + 1:]
            cause = fix = None
            wasted = len(later)
            retry = next((k for k, s in enumerate(later) if s.get("ok", True)
                          and s["tool"] == st["tool"] and s["input"] == st["input"]), None)
            if retry is not None:
                # the same command passed later, so the fix is the change made in between
                changes = [s for s in later[:retry] if s["tool"] in CHANGE_TOOLS and s.get("ok", True)]
                if changes:
                    fix = changes[-1]["input"]
                    earlier = [s["input"] for s in steps[:i] if s["tool"] == changes[-1]["tool"]]
                    if earlier:
                        cause = max(earlier, key=lambda e: difflib.SequenceMatcher(None, e, fix).ratio())
                wasted = retry + 1
            elif ep.get("outcome") == "success":
                # a different command worked in the end, so that command is the fix
                good = [s for s in later if s.get("ok", True) and s["tool"] == st["tool"]]
                if good:
                    cause, fix = st["input"], good[-1]["input"]
            if cause and fix and cause != fix:
                key = (ep.get("skill", ""), signature(st.get("output")), cause, fix)
                g = groups.setdefault(key, {"runs": [], "wasted": 0})
                g["runs"].append(ep["id"])
                g["wasted"] += wasted
            break  # only the first failure of each run
    return [{"skill": k[0], "error": k[1], "cause": k[2], "fix": k[3], **v}
            for k, v in groups.items() if len(v["runs"]) >= min_runs]


# ----------------------------------------------------------------- drafting


def draft_by_rules(skill, lesson):
    body = skill["body"]
    replaced = body.count(lesson["cause"])
    new = body.replace(lesson["cause"], lesson["fix"])
    note = (f"- `{lesson['error']}`: use `{lesson['fix']}`, not `{lesson['cause']}`. "
            f"Seen in {len(lesson['runs'])} runs.")
    if "## Known errors" in new:
        new = new.rstrip() + "\n" + note + "\n"
    else:
        new = new.rstrip() + "\n\n## Known errors\n\n" + note + "\n"
    what = (f"replaced the failing line in {replaced} place{'s' if replaced != 1 else ''}"
            if replaced else "no step used the failing line, so only a note was added")
    return {"body": new, "evidence": lesson["runs"],
            "summary": f"{len(lesson['runs'])} runs hit the same error and ended with the same fix; {what}."}


def draft_by_model(skill, episodes, rejections):
    key, model = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ATL_MODEL")
    if not key or not model:
        raise SystemExit("--live needs ANTHROPIC_API_KEY and ATL_MODEL (a model name) in your environment.")
    payload = {"skill": {"name": skill["name"], "body": skill["body"]}, "runs": episodes,
               "rejected_before": rejections}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 2000, "system": CONTRACT,
                         "messages": [{"role": "user", "content": json.dumps(payload)}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    reply = json.loads(text)
    if not reply.get("body"):
        return None
    known = {e["id"] for e in episodes}
    reply["evidence"] = [e for e in reply.get("evidence", []) if e in known]
    if not reply["evidence"]:
        raise SystemExit("The model's draft cited no runs from the input, so it was discarded.")
    return {"body": reply["body"].strip() + "\n", "summary": reply.get("summary", ""),
            "evidence": reply["evidence"]}


def induce(box, episodes, only=None, live=False):
    """Draft updates and park them as pending. Nothing changes for the agent yet."""
    pending = []
    lessons = find_lessons(episodes)
    for name in sorted({l["skill"] for l in lessons}):
        if (only and name != only) or name not in box.state["active"]:
            continue
        skill = box.get(name)
        mine = [l for l in lessons if l["skill"] == name]
        if live:
            runs = [e for e in episodes if e.get("skill") == name]
            rejected = [r["reason"] for r in box.state["reviews"]
                        if r["name"] == name and r["decision"] == "rejected"]
            draft = draft_by_model(skill, runs, rejected)
            by = os.environ.get("ATL_MODEL")
        else:
            draft = {"body": skill["body"], "evidence": [], "summary": ""}
            for lesson in mine:
                step = draft_by_rules(dict(skill, body=draft["body"]), lesson)
                draft = {"body": step["body"], "evidence": draft["evidence"] + step["evidence"],
                         "summary": (draft["summary"] + " " + step["summary"]).strip()}
            by = "rule-based reader"
        if draft and draft["body"] != skill["body"]:
            pending.append({"name": name, "from": skill["version"], "version": skill["version"] + 1,
                            "description": skill["description"], "drafted_by": by, **draft})
    box.state["pending"] = pending
    box.save()
    return pending


# ----------------------------------------------------------------- commands


def show_diff(old, new):
    for line in difflib.unified_diff(old.split("\n"), new.split("\n"), "current", "proposed", lineterm="", n=2):
        if line.startswith(("---", "+++", "@@")):
            print(paint("  " + line, "2"))
        elif line.startswith("+"):
            print(paint("  " + line, "32"))
        elif line.startswith("-"):
            print(paint("  " + line, "31"))
        else:
            print("  " + line)


def cmd_reset(a):
    box = Box(os.path.join(a.out, "skillbox"))
    n = box.seed(a.skills, a.traces)
    print(f"Skill box reset: {n} skills from {a.skills}")
    print(f"Traces: {a.traces}")


def cmd_list(a):
    box = open_box(a)
    heading("Skill box")
    for s in box.active():
        print(f"  {s['name']:<26} v{s['version']}   {s['description']}")
    if box.state["pending"]:
        print(f"\n  {len(box.state['pending'])} update waiting for review:  python3 run.py skills review")


def cmd_traces(a):
    box = open_box(a)
    episodes = load_traces(box.state["traces"])
    heading(f"Traces: {len(episodes)} past runs")
    for ep in episodes:
        failed = next((s for s in ep["steps"] if not s.get("ok", True)), None)
        status = paint("hit an error", "31") if failed else paint("clean", "32")
        print(f"  {ep['id']:<8} {ep.get('skill', ''):<16} {len(ep['steps'])} tool calls  {status}")
        if failed:
            print(f"           {signature(failed.get('output'))}")
    lessons = find_lessons(episodes)
    heading("Failures that repeat")
    if not lessons:
        print("  None found in at least two runs.")
    for l in lessons:
        print(f"  {l['skill']}: {len(l['runs'])} runs ({', '.join(l['runs'])})")
        print(f"    error   {l['error']}")
        print(f"    cause   {l['cause']}")
        print(f"    fix     {l['fix']}")
        print(f"    cost    {l['wasted']} extra tool calls spent recovering")


def cmd_search(a, box=None, label="Best match"):
    box = box or open_box(a)
    hit = box.search(a.task)[0]
    print(f"  Task: {a.task}")
    print(f"  {label}: {paint(hit['name'] + ' v' + str(hit['version']), '1')}   {hit['description']}\n")
    for line in hit["body"].rstrip().split("\n"):
        print("    " + line)
    return hit


def cmd_contract(a):
    heading("What the drafting model is told")
    print(CONTRACT)


def cmd_induce(a):
    box = open_box(a)
    pending = induce(box, load_traces(box.state["traces"]), only=a.skill, live=a.live)
    if not pending:
        print("No update to propose: no failure repeats in at least two runs.")
        return
    for p in pending:
        print(f"Drafted {p['name']} v{p['from']} -> v{p['version']} ({p['drafted_by']}). {p['summary']}")
    print("Saved as pending. Nothing is active until it is reviewed:  python3 run.py skills review")


def cmd_review(a):
    box = open_box(a)
    if not box.state["pending"]:
        print("Nothing is waiting for review. Run:  python3 run.py skills induce")
        return
    for p in list(box.state["pending"]):
        current = box.get(p["name"], p["from"])
        heading(f"Review: {p['name']} v{p['from']} -> v{p['version']}")
        print(f"  Drafted by  {p['drafted_by']}")
        print(f"  Why         {p['summary']}")
        print(f"  Evidence    runs {', '.join(p['evidence'])}\n")
        show_diff(current["body"], p["body"])
        decision, reason = a.decision, a.reason
        if not decision:
            answer = input("\n  Approve, reject or skip? [a/r/s] ").strip().lower()
            decision = {"a": "approved", "r": "rejected"}.get(answer[:1])
            if decision:
                reason = input("  Reason: ").strip()
        if not decision:
            print("  Skipped. It stays pending.")
            continue
        if decision == "approved":
            box.write({"name": p["name"], "version": p["version"], "description": p["description"],
                       "body": p["body"]})
            box.state["active"][p["name"]] = p["version"]
        box.state["pending"].remove(p)
        box.state["reviews"].append({"name": p["name"], "version": p["version"], "decision": decision,
                                     "reason": reason or "", "at": time.strftime("%Y-%m-%d %H:%M")})
        box.save()
        if decision == "approved":
            print(paint(f"\n  Approved. {p['name']} v{p['version']} is now the active skill.", "32"))
        else:
            print(paint(f"\n  Rejected. {p['name']} stays at v{p['from']}. The reason is logged, and a model-written draft is shown it next time.", "31"))


def cmd_demo(a):
    def pause(text):
        if not a.yes and sys.stdin.isatty():
            input(paint(f"\n[Enter] {text} ", "2"))

    box = Box(os.path.join(a.out, "skillbox"))
    box.seed(a.skills, a.traces)
    episodes = load_traces(box.state["traces"])
    lessons = find_lessons(episodes)
    if not lessons:
        raise SystemExit("These traces hold no failure that repeats, so there is nothing to demo.")
    lesson = lessons[0]
    a.task = next(e["task"] for e in episodes if e["id"] == lesson["runs"][0])
    a.skill, a.live = lesson["skill"], getattr(a, "live", False)

    cmd_traces(a)
    pause("see which skill the agent reads for this task today")
    heading("Before: the skill the agent follows today")
    cmd_search(a, box)
    pause("draft an update from the traces")
    heading("Draft")
    cmd_induce(a)
    pause("review it")
    cmd_review(a)
    box = Box(box.folder)
    heading("After: the skill the agent follows now")
    cmd_search(a, box)
    if box.state["active"][lesson["skill"]] > 1:
        print(f"\n  The next run starts from the fix. The {len(lesson['runs'])} earlier runs spent "
              f"{lesson['wasted']} extra tool calls finding it.")


def add_commands(sub):
    s = sub.add_parser("skills", help="turn repeated failures into a reviewed skill update")
    s = s.add_subparsers(dest="cmd", required=True)
    specs = [("demo", cmd_demo, "run the whole loop, pausing between steps"),
             ("reset", cmd_reset, "refill the skill box from a skills folder"),
             ("list", cmd_list, "show the active skills"),
             ("traces", cmd_traces, "show past runs and the failures that repeat"),
             ("search", cmd_search, "show the skill the agent would pick for a task"),
             ("contract", cmd_contract, "show the instructions given to the drafting model"),
             ("induce", cmd_induce, "draft skill updates from the traces"),
             ("review", cmd_review, "approve or reject pending updates")]
    for name, fn, text in specs:
        p = s.add_parser(name, help=text)
        p.add_argument("--out", default="out", help="where the skill box lives (default: out)")
        p.set_defaults(fn=fn)
        if name in ("demo", "reset"):
            p.add_argument("--skills", default=SAMPLE_SKILLS, help="folder of skill .md files to start from")
            p.add_argument("--traces", default=SAMPLE_TRACES, help="JSON Lines file of past runs")
        if name in ("demo", "induce"):
            p.add_argument("--live", action="store_true", help="have a language model write the draft")
        if name == "induce":
            p.add_argument("--skill", help="only this skill")
        if name == "search":
            p.add_argument("task")
        if name in ("demo", "review"):
            p.add_argument("--approve", dest="decision", action="store_const", const="approved")
            p.add_argument("--reject", dest="decision", action="store_const", const="rejected")
            p.add_argument("--reason", default="")
        if name == "demo":
            p.add_argument("--yes", action="store_true", help="do not pause between steps")
