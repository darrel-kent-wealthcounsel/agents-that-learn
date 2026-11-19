"""Audit the raw graph, clean it, and store it (two tables plus one file for the viewer)."""
import csv
import json
import os
import sqlite3
from collections import Counter

KINDS = ["contains", "import", "call", "api", "co_edit"]
KIND_LABEL = {"contains": "contains", "import": "imports", "call": "calls",
              "api": "API call", "co_edit": "changed together"}


def audit(nodes, edges, hashes=None):
    """Scorecard for a list of nodes and (src, dst, kind) edges."""
    ids = {n["id"] for n in nodes}
    seen, touched = Counter(edges), set()
    dangling = loops = 0
    for s, t, _ in edges:
        if s not in ids or t not in ids:
            dangling += 1
        elif s == t:
            loops += 1
        else:
            touched.update((s, t))
    by_kind = Counter(k for _, _, k in edges)
    dup_files = 0
    if hashes:
        dup_files = sum(c - 1 for c in Counter(hashes.values()).values() if c > 1)
    return {
        "files": sum(n["kind"] == "file" for n in nodes),
        "functions": sum(n["kind"] == "symbol" for n in nodes),
        "links": len(edges),
        "by_kind": {k: by_kind[k] for k in KINDS if by_kind[k]},
        "repeated_links": len(edges) - len(seen),
        "self_links": loops,
        "broken_links": dangling,
        "unconnected": len(ids - touched),
        "identical_files": dup_files,
    }


def restructure(nodes, edges):
    """Merge repeated links into one link with a count; drop self links and broken links."""
    ids = {n["id"] for n in nodes}
    merged = Counter((s, t, k) for s, t, k in edges if s in ids and t in ids and s != t)
    return [(s, t, k, c) for (s, t, k), c in merged.items()]


def pack(scan, clean, meta):
    """The single JSON document that `ask`, `eval` and the viewer all read."""
    nodes = scan["nodes"]
    index = {n["id"]: i for i, n in enumerate(nodes)}
    file_index = {n["path"]: i for i, n in enumerate(nodes) if n["kind"] == "file"}
    areas = sorted({n["area"] for n in nodes})
    out_nodes = []
    for n in nodes:
        item = {"id": n["id"], "l": n["label"], "f": file_index[n["path"]], "n": n["n"]}
        if n["kind"] == "file":
            item.update(k=0, path=n["path"], a=areas.index(n["area"]), p=n["p"])
        else:
            item.update(k=1, ln=n["line"])
            if n["d"]:
                item["d"] = n["d"]
        out_nodes.append(item)
    return {
        "meta": meta, "kinds": KINDS, "areas": areas, "nodes": out_nodes,
        "edges": [[index[s], index[t], KINDS.index(k), c] for s, t, k, c in clean],
        "vocab": scan["vocab"],
        "bags": {str(file_index[p]): flat for p, flat in scan["bags"].items()},
    }


def store(out_dir, scan, clean):
    """Two plain tables, as CSV files and as a SQLite database you can query."""
    os.makedirs(out_dir, exist_ok=True)
    node_rows = [(n["id"], n["kind"], n["label"], n["path"], n["line"]) for n in scan["nodes"]]
    with open(os.path.join(out_dir, "nodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "kind", "label", "path", "line"])
        w.writerows(node_rows)
    with open(os.path.join(out_dir, "edges.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["src", "dst", "kind", "count"])
        w.writerows(clean)
    db_path = os.path.join(out_dir, "graph.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, label TEXT, path TEXT, line INTEGER);
        CREATE TABLE edges (src TEXT REFERENCES nodes(id), dst TEXT REFERENCES nodes(id),
                            kind TEXT, count INTEGER);
        CREATE INDEX edges_src ON edges(src);
        CREATE INDEX edges_dst ON edges(dst);
    """)
    db.executemany("INSERT INTO nodes VALUES (?,?,?,?,?)", node_rows)
    db.executemany("INSERT INTO edges VALUES (?,?,?,?)", clean)
    db.commit()
    db.close()


def load(out_dir):
    path = os.path.join(out_dir, "graph.json")
    if not os.path.exists(path):
        raise SystemExit(f"No graph at {path}. Run:  python3 run.py graph build --repo <path>")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
