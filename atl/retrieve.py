"""Two ways to find files for a question.

keyword: rank files by how often they contain the question's words.
graph:   1. anchor - pick the file or function whose name best matches the question
         2. walk   - personalised PageRank from the anchor over the links

viewer.html carries the same scoring in JavaScript; change both together.
"""
import math
import random
from collections import defaultdict, deque

from .text import query_tokens, words

KIND_WEIGHT = [1.0, 1.0, 1.0, 1.0, 0.5]  # contains, import, call, api, co_edit
RESTART = 0.15
ROUNDS = 40


class Index:
    def __init__(self, g):
        self.g = g
        self.nodes = nodes = g["nodes"]
        self.files = [i for i, n in enumerate(nodes) if n["k"] == 0]
        self.adj = [[] for _ in nodes]
        for s, t, k, c in g["edges"]:
            w = KIND_WEIGHT[k] * (1 + math.log(c))
            self.adj[s].append((t, w, k))
            self.adj[t].append((s, w, k))
        self.out = [sum(w for _, w, _ in a) for a in self.adj]
        # how rare each word is among node names and paths
        df = defaultdict(int)
        for n in nodes:
            for t in self._all_tokens(n):
                df[t] += 1
        self.idf = {t: math.log(1 + len(nodes) / c) for t, c in df.items()}
        # word counts per file, for keyword search
        vocab = g["vocab"]
        self.bags = {}
        fdf = defaultdict(int)
        for fi, flat in g["bags"].items():
            bag = {vocab[flat[i]]: flat[i + 1] for i in range(0, len(flat), 2)}
            self.bags[int(fi)] = bag
            for t in bag:
                fdf[t] += 1
        self.fidf = {t: math.log(1 + len(self.bags) / c) for t, c in fdf.items()}

    def _all_tokens(self, n):
        f = self.nodes[n["f"]]
        return set(n["n"]) | set(n.get("d", ())) | set(f["n"]) | set(f["p"])

    def path_of(self, i):
        return self.nodes[self.nodes[i]["f"]]["path"]

    # ------------------------------------------------------------ step 1: anchor
    def _keyword_scores(self, q):
        scores = {}
        for fi, bag in self.bags.items():
            s = sum(self.fidf.get(t, 0) * bag[t] / (bag[t] + 1.5) for t in q if t in bag)
            if s:
                scores[fi] = s
        return scores

    def anchors(self, question, n=1):
        """Best match for the question: mostly the name, partly the words in its file."""
        q = query_tokens(question)
        kw = self._keyword_scores(q)
        kmax = max(kw.values(), default=0)
        named = []
        for i, node in enumerate(self.nodes):
            f = self.nodes[node["f"]]
            name = set(node["n"])
            path = set(f["p"]) | (set(f["n"]) if node["k"] else set())
            doc = set(node.get("d", ()))
            score, hit = 0.0, 0
            for t in q:
                w = self.idf.get(t, 0)
                if t in name:
                    score += 2.0 * w
                    hit += 1
                elif t in doc:
                    score += 0.6 * w
                elif t in path:
                    score += 0.4 * w
            named.append(score * (hit + 0.5) / (len(name) + 0.5))  # names the question covers fully win
        nmax = max(named, default=0)
        scored = []
        for i, node in enumerate(self.nodes):
            if not named[i] and node["k"]:
                continue  # a function with no name match is never the anchor; its file can be
            total = (named[i] / nmax if nmax else 0) + (0.3 * kw.get(node["f"], 0) / kmax if kmax else 0)
            if total:
                scored.append((-total, -node["k"], i))
        scored.sort()
        picked = scored[:n]
        for item in scored[n:3]:  # exact ties start the walk too
            if abs(item[0] - scored[0][0]) < 1e-9:
                picked.append(item)
        return [(i, -s) for s, _, i in picked]

    # ------------------------------------------------------------ step 2: walk
    def pagerank(self, seeds):
        """Random walk that keeps jumping back to the seeds; returns a score per node."""
        n = len(self.nodes)
        start = [0.0] * n
        for s in seeds:
            start[s] = 1.0 / len(seeds)
        rank = start[:]
        for _ in range(ROUNDS):
            nxt = [RESTART * v for v in start]
            for i, r in enumerate(rank):
                if r and self.out[i]:
                    share = (1 - RESTART) * r / self.out[i]
                    for j, w, _ in self.adj[i]:
                        nxt[j] += share * w
            rank = nxt
        return rank

    def graph_search(self, question, k=5, n_anchors=1):
        found = self.anchors(question, n_anchors)
        if not found:
            return {"anchors": [], "files": []}
        rank = self.pagerank([i for i, _ in found])
        per_file = defaultdict(float)
        for i, r in enumerate(rank):
            if r:
                per_file[self.nodes[i]["f"]] += r
        top = sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return {"anchors": [i for i, _ in found], "files": top}

    def keyword_search(self, question, k=5):
        scores = self._keyword_scores(query_tokens(question))
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]

    def chain(self, starts, file_index):
        """Shortest run of links from any anchor to any node in the given file."""
        prev = {s: None for s in starts}
        queue = deque(starts)
        while queue:
            i = queue.popleft()
            if self.nodes[i]["f"] == file_index:
                steps = []
                while prev[i] is not None:
                    steps.append((i, prev[i][1]))
                    i = prev[i][0]
                return [(i, None)] + steps[::-1]
            for j, _, kind in self.adj[i]:
                if j not in prev:
                    prev[j] = (i, kind)
                    queue.append(j)
        return []


# ---------------------------------------------------------------- comparison


def make_tasks(ix, per_kind=40, seed=0):
    """Questions built from the graph itself. Useful for comparing methods, not for accuracy claims."""
    nodes = ix.nodes
    name_count = defaultdict(int)
    for n in nodes:
        name_count[n["l"]] += 1
    named = [i for i, n in enumerate(nodes)
             if n["k"] == 1 and len(n["n"]) >= 2 and name_count[n["l"]] == 1]
    rng = random.Random(seed)
    easy = [{"q": " ".join(words(nodes[i]["l"])), "want": {nodes[i]["f"]}}
            for i in rng.sample(named, min(per_kind, len(named)))]
    hops = []
    follow = {2, 3}  # call, api
    for a in named:
        fa = nodes[a]["f"]
        want = set()
        for b, _, k in ix.adj[a]:
            if k in follow and nodes[b]["f"] != fa:
                for c, _, k2 in ix.adj[b]:
                    if k2 in follow and nodes[c]["f"] not in (fa, nodes[b]["f"]):
                        want.update((nodes[b]["f"], nodes[c]["f"]))
        if want:
            hops.append({"q": " ".join(words(nodes[a]["l"])), "want": want})
    rng.shuffle(hops)
    return {"easy": easy, "multi-hop": hops[:per_kind]}


def evaluate(ix, k=5, n_anchors=1):
    rows = []
    for kind, tasks in make_tasks(ix).items():
        kw = gr = 0.0
        for t in tasks:
            want = t["want"]
            kw += len(want & {f for f, _ in ix.keyword_search(t["q"], k)}) / len(want)
            gr += len(want & {f for f, _ in ix.graph_search(t["q"], k, n_anchors)["files"]}) / len(want)
        n = len(tasks)
        rows.append((kind, n, kw / n if n else 0.0, gr / n if n else 0.0))
    return rows
