"""Read a repo into nodes (files, functions) and edges (contains, import, call, api, co_edit).

JavaScript, TypeScript and Vue are read by pattern, not by a compiler, so some calls
are missed and a few are wrong. Python is read with the standard `ast` module.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import subprocess
from collections import Counter, defaultdict

from .text import tokens

SKIP_DIRS = {
    "node_modules", "dist", "build", "out", "coverage", "venv", "__pycache__", "vendor",
    "cdk.out", "storybook-static", "target", "site-packages",
}
JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")
CODE_EXT = JS_EXT + (".py",)
MAX_BYTES = 400_000
ID = r"[A-Za-z_$][\w$]*"

# ----------------------------------------------------------------- file listing


def list_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and (not d.startswith(".") or d == ".storybook")
        )
        for name in sorted(filenames):
            if not name.endswith(CODE_EXT) or name.endswith((".d.ts", ".min.js")):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_BYTES:
                    continue
            except OSError:
                continue
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return found


def area_of(path, deep=False):
    """The part of the repo a file belongs to (used for colour on the map)."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if (deep or parts[0] in ("src", "packages", "apps", "app", "lib")) and len(parts) > 2:
        return parts[0] + "/" + parts[1]
    return parts[0]


# ----------------------------------------------------------------- JS / TS / Vue

_LEX = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`", re.S
)
_NOT_NL = re.compile(r"[^\n]")


def blank(src, strings=True):
    """Replace comments (and string contents) with spaces. Offsets and lines stay put."""
    def sub(m):
        text = m.group(0)
        if text[0] == "/" or strings:
            return _NOT_NL.sub(" ", text)
        return text
    return _LEX.sub(sub, src)


def vue_script(src):
    """Keep only the <script> contents of a .vue file, at their original offsets."""
    out = [ch if ch == "\n" else " " for ch in src]
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", src, re.S):
        out[m.start(1):m.end(1)] = src[m.start(1):m.end(1)]
    return "".join(out)


IMPORT_RE = re.compile(
    r"""\b(?:import|export)\s+(?:type\s+)?([\w$*{}\s,]*?)\s*\bfrom\s*['"]([^'"\n]+)['"]"""
    r"""|\bimport\s*['"]([^'"\n]+)['"]"""
    r"""|\b(?:require|import)\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""
)


def parse_clause(clause):
    """`Foo, { a, b as c }` -> {'Foo': 'default', 'a': 'a', 'c': 'b'}"""
    names = {}
    m = re.search(r"\{([^}]*)\}", clause)
    if m:
        for part in m.group(1).split(","):
            part = re.sub(r"^\s*type\s+", "", part.strip())
            if part:
                bits = re.split(r"\s+as\s+", part)
                names[bits[-1].strip()] = bits[0].strip()
        clause = clause[:m.start()] + clause[m.end():]
    m = re.search(r"\*\s*as\s+([\w$]+)", clause)
    if m:
        names[m.group(1)] = "*"
        clause = clause[:m.start()] + clause[m.end():]
    rest = clause.strip(" ,\n\t")
    if rest == "*":
        names["*"] = "*"
    elif re.fullmatch(r"[\w$]+", rest or ""):
        names[rest] = "default"
    return names


DEF_RES = [
    ("fn", re.compile(rf"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*({ID})\s*[(<]", re.M)),
    ("fn", re.compile(
        rf"^[ \t]*(?:export\s+)?(?:const|let|var)\s+({ID})\s*(?::[^=\n]+)?=\s*(?:async\s*)?"
        rf"(?:function\b|\([^()]*\)\s*(?::[^=\n]+?)?=>|{ID}\s*=>)", re.M)),
    ("class", re.compile(rf"^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+({ID})", re.M)),
    ("value", re.compile(rf"^[ \t]*export\s+(?:const|let|var)\s+({ID})\s*(?::[^=\n]+)?=", re.M)),
    ("fn", re.compile(
        rf"^[ \t]+(?:(?:public|private|protected|static|readonly|override)\s+)*(?:async\s+)?"
        rf"(?:get\s+|set\s+)?\*?({ID})\s*(?:<[^>\n]*>)?\([^()]*\)\s*(?::\s*[^{{;\n=]+)?\{{", re.M)),
]
NOT_NAMES = set(
    "if for while switch catch function return constructor else do try with await super new "
    "typeof delete void throw case in of yield import export default".split()
)
CALL_RE = re.compile(rf"(?:({ID})\s*\.\s*)?({ID})\s*\(")
_CONTINUES = set("=>,(:|&?+[{")


def body_end(code, start, limit=30000):
    """Find where the definition starting at `start` ends (rough bracket matching)."""
    depth, last = 0, ""
    end = min(len(code), start + limit)
    for i in range(start, end):
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return i
            if depth == 0 and ch == "}":
                return i + 1
        elif depth == 0 and (ch == ";" or (ch == "\n" and last and last not in _CONTINUES)):
            return i
        if not ch.isspace():
            last = ch
    return end


def doc_above(lines, line_no):
    """Comment text directly above a definition (line_no is 1-based)."""
    got = []
    i = line_no - 2
    while i >= 0 and len(got) < 8:
        s = lines[i].strip()
        if s.startswith(("//", "/*", "*", "#")):
            got.append(s.lstrip("/*# ").rstrip("*/ "))
            i -= 1
        else:
            break
    return " ".join(reversed(got))[:240]


_SEG = r"(?:[\w\-.]+|:\w+|\{[^}'\"`]*\}|\$\{[^}'\"`]*\})"
ROUTE_RE = re.compile(rf"""(['"`])(/{_SEG}(?:/{_SEG})*/?)\1""")
METHOD_BEFORE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b|\.(get|post|put|patch|delete)\s*\(\s*$")
METHOD_AFTER = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b")


def find_routes(text):
    """(offset, 'POST /orders/{}/payment') for path strings with an HTTP method close by."""
    out = []
    for m in ROUTE_RE.finditer(text):
        path = re.sub(r":\w+|\$?\{[^}]*\}", "{}", m.group(2)).rstrip("/")
        if not re.search(r"[A-Za-z]", path.replace("{}", "")):
            continue
        hit = None
        for mm in METHOD_BEFORE.finditer(text[max(0, m.start() - 90):m.start()]):
            hit = mm
        if hit is None:
            hit = METHOD_AFTER.search(text[m.end():m.end() + 60])
        if hit:
            method = next(g for g in hit.groups() if g).upper()
            out.append((m.start(), f"{method} {path}"))
    return out


def parse_js(path, src):
    script = vue_script(src) if path.endswith(".vue") else src
    nocomment = blank(script, strings=False)
    code = blank(script, strings=True)
    lines = src.split("\n")

    imports, reexports = [], []
    for m in IMPORT_RE.finditer(nocomment):
        spec = m.group(2) or m.group(3) or m.group(4)
        names = parse_clause(m.group(1) or "")
        if m.group(0).lstrip().startswith("export"):
            reexports.append((spec, None if "*" in names else dict(names)))
        imports.append((spec, names))

    symbols, spans = {}, []
    for kind, rx in DEF_RES:
        for m in rx.finditer(code):
            name = m.group(1)
            if name in NOT_NAMES or name in symbols:
                continue
            start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
            ln = code.count("\n", 0, start) + 1
            symbols[name] = {"name": name, "kind": kind, "line": ln, "doc": doc_above(lines, ln)}
            spans.append((start, body_end(code, start), name, m.end()))
    spans.sort()

    def owner(pos):
        best = None
        for start, end, name, _ in spans:
            if start > pos:
                break
            if pos < end:
                best = name
        return best

    heads = [(s, e) for s, _, _, e in spans]
    calls = []
    for m in CALL_RE.finditer(code):
        ns, name = m.group(1), m.group(2)
        if name in NOT_NAMES or any(s <= m.start(2) < e for s, e in heads):
            continue
        calls.append((owner(m.start()), ns, name))
    if path.endswith(".vue"):  # calls made from the template belong to the file
        template = blank("".join(a if b.isspace() else " " for a, b in zip(src, script)))
        calls += [(None, m.group(1), m.group(2)) for m in CALL_RE.finditer(template)
                  if m.group(2) not in NOT_NAMES]
    routes = [(owner(pos), key) for pos, key in find_routes(nocomment)]
    return {"imports": imports, "reexports": reexports, "symbols": symbols, "calls": calls,
            "routes": routes, "lang": "js"}


# ----------------------------------------------------------------- Python


def parse_py(path, src):
    empty = {"imports": [], "reexports": [], "symbols": {}, "calls": [], "routes": [], "lang": "py"}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return empty
    imports, symbols, calls = [], {}, []

    def add_calls(node, owner):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    calls.append((owner, None, f.id))
                elif isinstance(f, ast.Attribute):
                    ns = f.value.id if isinstance(f.value, ast.Name) else "?"
                    calls.append((owner, ns, f.attr))

    def add_def(node):
        doc = (ast.get_docstring(node) or "").strip().split("\n")[0][:240]
        is_class = isinstance(node, ast.ClassDef)
        symbols.setdefault(node.name, {"name": node.name, "line": node.lineno, "doc": doc,
                                       "kind": "class" if is_class else "fn"})
        if is_class:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_def(item)
        else:
            add_calls(node, node.name)

    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append((a.name, {(a.asname or a.name.split(".")[0]): "*"}, 0))
        elif isinstance(node, ast.ImportFrom):
            names = {(a.asname or a.name): a.name for a in node.names}
            imports.append((node.module or "", names, node.level))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add_def(node)
        else:
            add_calls(node, None)
    return dict(empty, imports=imports, symbols=symbols, calls=calls,
                routes=[(None, key) for _, key in find_routes(src)])


# ----------------------------------------------------------------- import resolution


class Resolver:
    def __init__(self, root, files):
        self.root, self.files, self._cfg = root, set(files), {}

    def _try(self, p):
        p = posixpath.normpath(p)
        if p.startswith(".."):
            return None
        cands = [p] + [p + e for e in JS_EXT] + [p + "/index" + e for e in JS_EXT]
        stem, ext = posixpath.splitext(p)
        if ext in (".js", ".mjs", ".cjs", ".jsx"):
            cands += [stem + e for e in (".ts", ".tsx", ".mts", ".vue")]
        for c in cands:
            if c in self.files:
                return c
        return None

    def _config(self, d):
        """Nearest tsconfig/jsconfig going up from directory d: (dir, [(prefix, target, wild)])."""
        if d in self._cfg:
            return self._cfg[d]
        result = None
        for name in ("tsconfig.json", "jsconfig.json"):
            full = os.path.join(self.root, d, name)
            if os.path.isfile(full):
                pairs = []
                try:
                    with open(full, encoding="utf-8", errors="ignore") as f:
                        raw = f.read()
                    raw = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                                 lambda m: m.group(0) if m.group(0)[0] == '"' else "", raw, flags=re.S)
                    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
                    opts = json.loads(raw).get("compilerOptions", {})
                    base = posixpath.join(d, opts.get("baseUrl", "."))
                    for key, targets in (opts.get("paths") or {}).items():
                        for t in targets:
                            pairs.append((key.rstrip("*"), posixpath.join(base, t.rstrip("*")),
                                          key.endswith("*")))
                except (ValueError, OSError, AttributeError):
                    pass
                result = (d, pairs)
                break
        if result is None:
            if d in ("", ".") or os.path.isfile(os.path.join(self.root, d, "package.json")):
                result = (d, [])
            else:
                result = self._config(posixpath.dirname(d))
        self._cfg[d] = result
        return result

    def js(self, spec, from_file):
        d = posixpath.dirname(from_file)
        if spec.startswith("."):
            return self._try(posixpath.join(d, spec))
        pkg, pairs = self._config(d)
        for prefix, target, wild in pairs:
            if (wild and spec.startswith(prefix)) or spec == prefix:
                hit = self._try(posixpath.join(target, spec[len(prefix):]) if wild else target)
                if hit:
                    return hit
        m = re.match(r"^[@~#]/(.*)", spec)
        rest = m.group(1) if m else spec
        if m or "/" in spec:
            for base in (posixpath.join(pkg, "src"), pkg, "src", ""):
                hit = self._try(posixpath.join(base, rest))
                if hit:
                    return hit
        return None

    def py(self, module, level, from_file):
        d = posixpath.dirname(from_file)
        if level:
            for _ in range(level - 1):
                d = posixpath.dirname(d)
            bases = [d]
        else:
            bases, cur = ["", "src"], d
            while cur:
                bases.append(cur)
                cur = posixpath.dirname(cur)
        parts = module.split(".")
        # also try without the leading package name, for when the scan starts inside the package
        for skip in range(1 if level else min(2, len(parts))):
            rel = "/".join(parts[skip:])
            for b in bases:
                for c in (posixpath.join(b, rel + ".py"), posixpath.join(b, rel, "__init__.py")):
                    c = posixpath.normpath(c)
                    if c in self.files:
                        return c
        return None


# ----------------------------------------------------------------- git history


def git_coedits(root, files, max_commits=3000, max_files=25, min_count=2):
    """Pairs of files that appear in the same commits: {(a, b): count}. None if no git."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "log", "--no-merges", "--name-only", "--relative",
             f"-n{max_commits}", "--pretty=format:%x01"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    pairs = Counter()
    for chunk in out.stdout.split("\x01"):
        names = sorted({n for n in chunk.split("\n") if n in files})
        if 2 <= len(names) <= max_files:
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    pairs[(a, b)] += 1
    return {k: v for k, v in pairs.items() if v >= min_count}


# ----------------------------------------------------------------- the scan itself

SERVERISH = re.compile(r"handler|lambda|route|controller|server|backend|functions|api/", re.I)
TESTISH = re.compile(r"\.(test|spec|stories)\.|(^|/)(tests?|__tests__|stories)/", re.I)
BUILTIN = set("push map filter forEach reduce includes indexOf slice splice concat join keys values "
              "entries then catch finally toString valueOf append extend update format".split())


def scan(root, with_git=True, log=print):
    files = list_files(root)
    fileset = set(files)
    res = Resolver(root, fileset)
    parsed, hashes, bags = {}, {}, {}
    for path in files:
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="ignore") as f:
                src = f.read()
        except OSError:
            continue
        hashes[path] = hashlib.sha1(src.encode()).hexdigest()
        bags[path] = Counter(t for t in tokens(src) if len(t) >= 3)
        parsed[path] = parse_py(path, src) if path.endswith(".py") else parse_js(path, src)

    nodes, edges = [], []
    deep = len({area_of(p) for p in parsed}) < 5  # few top-level folders: colour by two levels
    area = lambda p: area_of(p, deep)
    fid = lambda p: "file:" + p
    sid = lambda p, n: f"fn:{p}#{n}"
    by_name = defaultdict(list)
    for path, info in parsed.items():
        base = posixpath.basename(path)
        nodes.append({"id": fid(path), "kind": "file", "label": base, "path": path, "line": 0,
                      "area": area(path), "n": tokens(posixpath.splitext(base)[0]),
                      "p": tokens(posixpath.dirname(path)), "d": []})
        for name, s in info["symbols"].items():
            label = name + ("()" if s["kind"] == "fn" else "")
            nodes.append({"id": sid(path, name), "kind": "symbol", "label": label, "path": path,
                          "line": s["line"], "area": area(path), "n": tokens(name),
                          "p": tokens(path), "d": tokens(s["doc"])[:40]})
            edges.append((fid(path), sid(path, name), "contains"))
            by_name[name].append(path)

    # imports: which file each local name points to
    name_map = {}
    for path, info in parsed.items():
        local, re_targets = {}, []
        for imp in info["imports"]:
            if info["lang"] == "py":
                module, names, level = imp
                target = None
                for loc, orig in names.items():
                    sub = None
                    if orig != "*":
                        sub = res.py((module + "." if module else "") + orig, level, path)
                    if sub:
                        edges.append((fid(path), fid(sub), "import"))
                        local[loc] = (sub, "*")
                    else:
                        target = target or res.py(module, level, path)
                        if target:
                            local[loc] = (target, orig)
                if target and target != path:
                    edges.append((fid(path), fid(target), "import"))
            else:
                spec, names = imp
                target = res.js(spec, path)
                if target and target != path:
                    edges.append((fid(path), fid(target), "import"))
                    for loc, orig in names.items():
                        local[loc] = (target, orig)
        for spec, names in info["reexports"]:
            target = res.js(spec, path)
            if target:
                re_targets.append((target, names))
        name_map[path] = (local, re_targets)

    def lookup(path, name, depth=0):
        if name in parsed[path]["symbols"]:
            return sid(path, name)
        if depth < 4:
            for target, names in name_map[path][1]:
                if names is None or name in names:
                    hit = lookup(target, names.get(name, name) if names else name, depth + 1)
                    if hit:
                        return hit
        return None

    for path, info in parsed.items():
        local = name_map[path][0]
        for owner, ns, name in info["calls"]:
            dst = None
            if ns is None:
                if name in local:
                    target, orig = local[name]
                    dst = lookup(target, name if orig in ("default", "*") else orig) or lookup(target, name)
                elif name in info["symbols"]:
                    dst = sid(path, name)
            elif ns in ("this", "self"):
                dst = sid(path, name) if name in info["symbols"] else None
            elif ns in local:
                target, orig = local[ns]
                dst = lookup(target, name)
                if dst is None and orig not in ("*", "default"):
                    holder = lookup(target, orig)  # e.g. an exported object behind a barrel file
                    if holder:
                        dst = lookup(holder[3:].rsplit("#", 1)[0], name)
            if dst is None and ns is not None and len(name) >= 6 and name not in BUILTIN:
                owners = by_name.get(name, [])
                if len(owners) == 1:  # only one thing in the repo has this name
                    dst = sid(owners[0], name)
            src_id = sid(path, owner) if owner else fid(path)
            if dst and dst != src_id:
                edges.append((src_id, dst, "call"))

    # caller -> server handler, matched by HTTP method and path
    routes = defaultdict(set)
    for path, info in parsed.items():
        if TESTISH.search(path):
            continue
        for owner, key in info["routes"]:
            routes[key].add((path, owner))
    for key, uses in routes.items():
        if not 2 <= len({p for p, _ in uses}) <= 4:
            continue
        servers = [u for u in uses if SERVERISH.search(u[0])]
        clients = [u for u in uses if not SERVERISH.search(u[0])]
        if not servers or not clients:
            ordered = sorted(uses, key=lambda u: (u[0], u[1] or ""))
            clients, servers = ordered[:1], ordered[1:]
        for cp, co in clients:
            for sp, so in servers:
                if cp != sp:
                    edges.append((sid(cp, co) if co else fid(cp), sid(sp, so) if so else fid(sp), "api"))

    co = git_coedits(root, fileset) if with_git else None
    if co is None:
        log("  no git history found here, so no 'changed together' links")
    else:
        for (a, b), count in co.items():
            edges.extend([(fid(a), fid(b), "co_edit")] * count)

    vocab, packed = {}, {}
    for path, bag in bags.items():
        flat = []
        for tok, count in bag.items():
            flat += [vocab.setdefault(tok, len(vocab)), count]
        packed[path] = flat
    return {"nodes": nodes, "edges": edges, "hashes": hashes, "bags": packed,
            "vocab": list(vocab), "has_git": co is not None}
