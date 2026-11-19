"""Command line for both demos. Standard library only."""
import argparse
import json
import os
import sys
import time
import webbrowser

from . import graph as G
from . import bench as B
from . import skills as S
from .retrieve import Index, evaluate
from .scan import scan
from .text import words

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_REPO = os.path.join(HERE, "sample", "shop")


def _scorecard(before, after):
    rows = [("files", "files"), ("functions", "functions"), ("links", "links"),
            ("repeated links", "repeated_links"), ("self links", "self_links"),
            ("broken links", "broken_links"), ("unconnected files/functions", "unconnected"),
            ("identical files", "identical_files")]
    print(f"\n  {'':30}{'before':>10}{'after':>10}")
    for label, key in rows:
        print(f"  {label:30}{before[key]:>10,}{after[key]:>10,}")
    kinds = ", ".join(f"{G.KIND_LABEL[k]} {v:,}" for k, v in after["by_kind"].items())
    print(f"  links by kind after clean-up: {kinds}")


def _examples(ix, given):
    """A few starter questions for the viewer: yours first, then busy functions."""
    out = list(given or [])
    busy = sorted(
        (i for i, n in enumerate(ix.nodes) if n["k"] == 1 and len(n["n"]) >= 2),
        key=lambda i: -sum(1 for _, _, k in ix.adj[i] if k in (2, 3)))
    for i in busy:
        q = " ".join(words(ix.nodes[i]["l"]))
        if q not in out:
            out.append(q)
        if len(out) >= 4:
            break
    return out


def graph_build(a):
    repo = os.path.abspath(os.path.expanduser(a.repo))
    if not os.path.isdir(repo):
        raise SystemExit(f"Not a folder: {repo}")
    t0 = time.time()
    print(f"Scanning {repo}")
    raw = scan(repo, with_git=not a.no_git)
    if not raw["nodes"]:
        raise SystemExit("No .ts, .js, .vue or .py files found there.")
    before = G.audit(raw["nodes"], raw["edges"], raw["hashes"])
    clean = G.restructure(raw["nodes"], raw["edges"])
    after = G.audit(raw["nodes"], [(s, t, k) for s, t, k, _ in clean], raw["hashes"])
    _scorecard(before, after)
    meta = {"repo": os.path.basename(repo.rstrip(os.sep)), "built": time.strftime("%Y-%m-%d"),
            "seconds": 0, "before": before, "after": after, "has_git": raw["has_git"], "examples": []}
    doc = G.pack(raw, clean, meta)
    given = a.question or (["where does a payment start?"] if repo == os.path.abspath(SAMPLE_REPO) else [])
    meta["examples"] = _examples(Index(doc), given)
    G.store(a.out, raw, clean)
    meta["seconds"] = max(0.1, round(time.time() - t0, 1))
    with open(os.path.join(a.out, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    with open(os.path.join(os.path.dirname(__file__), "viewer.html"), encoding="utf-8") as f:
        template = f.read()
    html = template.replace("__GRAPH_JSON__", json.dumps(doc, separators=(",", ":")).replace("</", "<\\/"))
    with open(os.path.join(a.out, "graph.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nBuilt in {meta['seconds']} s. Wrote graph.html, graph.json, graph.db, nodes.csv and edges.csv to {a.out}/")
    print("Next:")
    print(f"  python3 run.py graph open")
    print(f"  python3 run.py graph ask \"{meta['examples'][0] if meta['examples'] else 'your question'}\"")


def graph_ask(a):
    ix = Index(G.load(a.out))
    nodes = ix.nodes
    res = ix.graph_search(a.question, a.k, a.anchors)
    kw = ix.keyword_search(a.question, a.k)
    print(f"\nQuestion: {a.question}\n")
    if not res["anchors"]:
        print("Nothing in this repo has a name matching those words.")
        return
    anchor = res["anchors"][0]
    for n, i in enumerate(res["anchors"]):
        node = nodes[i]
        where = ix.path_of(i) + (f":{node['ln']}" if node["k"] else "")
        print(f"{'1. Anchor  ' if n == 0 else '           '}{node['l']}  ({where})")
    if len(res["anchors"]) > 1:
        print("           These names match equally well, so the walk starts from all of them.")
    print(f"\n2. Walk    top {len(res['files'])} files by PageRank from the anchor")
    kw_files = {f for f, _ in kw}
    verbs = ["in", "import", "call", "API call", "changed together"]
    fresh = 0
    for rank, (f, score) in enumerate(res["files"], 1):
        miss = f not in kw_files
        fresh += miss
        print(f"   {rank}. {nodes[f]['path']:<52} {score:.3f}{'   <- keyword search misses this' if miss else ''}")
        steps = ix.chain(res["anchors"], f)
        if len(steps) > 1:
            text = "".join((f"  ~{verbs[k]}~  " if k is not None else "") + nodes[i]["l"] for i, k in steps)
            print(f"      {text}")
    print(f"\nKeyword search, top {len(kw)} files")
    for rank, (f, _) in enumerate(kw, 1):
        print(f"   {rank}. {nodes[f]['path']}")
    print(f"\n{fresh} of the graph's {len(res['files'])} files are not in keyword search's list.")


def graph_eval(a):
    ix = Index(G.load(a.out))
    print(f"\nShare of expected files found in the top {a.k} (questions are generated from the graph itself)\n")
    print(f"  {'question type':16}{'questions':>10}{'keyword':>10}{'graph':>10}")
    for kind, n, kw, gr in evaluate(ix, a.k, a.anchors):
        print(f"  {kind:16}{n:>10}{kw:>10.0%}{gr:>10.0%}")
    print("\n  easy      = the question uses the words in the function's own name")
    print("  multi-hop = the expected files are one or two calls away from that function")


def graph_open(a):
    path = os.path.abspath(os.path.join(a.out, "graph.html"))
    if not os.path.exists(path):
        raise SystemExit("No map yet. Run:  python3 run.py graph build --repo <path>")
    print(path)
    webbrowser.open("file://" + path)


def main(argv=None):
    p = argparse.ArgumentParser(prog="run.py", description="Agents that learn: code graph and skill update demos.")
    sub = p.add_subparsers(dest="area", required=True)

    g = sub.add_parser("graph", help="build and search a code knowledge graph").add_subparsers(dest="cmd", required=True)
    b = g.add_parser("build", help="scan a repo and write the map")
    b.add_argument("--repo", default=SAMPLE_REPO, help="folder to scan (default: the bundled sample)")
    b.add_argument("--no-git", action="store_true", help="skip 'changed together' links")
    b.add_argument("--question", action="append", help="starter question for the map (repeatable)")
    b.set_defaults(fn=graph_build)
    q = g.add_parser("ask", help="anchor + walk, beside keyword search")
    q.add_argument("question")
    q.set_defaults(fn=graph_ask)
    e = g.add_parser("eval", help="compare keyword search and the graph on generated questions")
    e.set_defaults(fn=graph_eval)
    o = g.add_parser("open", help="open the map in your browser")
    o.set_defaults(fn=graph_open)
    for sp in (b, q, e, o):
        sp.add_argument("--out", default="out", help="where the graph files live (default: out)")
    for sp in (q, e):
        sp.add_argument("-k", type=int, default=5, help="how many files to return")
        sp.add_argument("--anchors", type=int, default=1, help="how many anchors to start from")

    S.add_commands(sub)
    B.add_commands(sub)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except BrokenPipeError:
        sys.exit(0)
