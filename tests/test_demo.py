"""Run with:  python3 -m unittest discover tests"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atl import graph as G                     # noqa: E402
from atl import skills as S                    # noqa: E402
from atl.cli import main                       # noqa: E402
from atl.retrieve import Index, evaluate       # noqa: E402
from atl.scan import scan                      # noqa: E402

SHOP = os.path.join(ROOT, "sample", "shop")
quiet = lambda *a, **k: None


class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = scan(SHOP, with_git=False, log=quiet)
        cls.edges = set(cls.raw["edges"])
        clean = G.restructure(cls.raw["nodes"], cls.raw["edges"])
        cls.ix = Index(G.pack(cls.raw, clean, {}))

    def test_import_through_alias(self):
        self.assertIn(("file:src/pages/PaymentPage.vue", "file:src/composables/usePaymentFlow.ts", "import"), self.edges)

    def test_call_through_barrel_file(self):
        self.assertIn(("fn:src/stores/orderActions.ts#startPayment",
                       "fn:src/services/orderService.ts#startPayment", "call"), self.edges)

    def test_call_from_vue_template(self):
        self.assertIn(("file:src/components/PaymentSummary.vue",
                       "fn:src/utils/paymentFormat.ts#paymentLabel", "call"), self.edges)

    def test_python_call(self):
        self.assertIn(("fn:tools/report.py#build_report", "fn:tools/billing/totals.py#daily_total", "call"), self.edges)

    def test_api_link_crosses_into_the_backend(self):
        api = {(s.split("#")[0], t.split("#")[0]) for s, t, k in self.edges if k == "api"}
        self.assertIn(("fn:src/services/orderService.ts", "fn:backend/bff/handlers/startPayment.ts"), api)
        self.assertIn(("fn:backend/bff/handlers/startPayment.ts", "fn:backend/dal/handlers/confirmCharge.ts"), api)

    def test_anchor_and_walk(self):
        res = self.ix.graph_search("where does a payment start?")
        anchor = self.ix.nodes[res["anchors"][0]]
        self.assertEqual(anchor["id"], "fn:src/stores/orderActions.ts#startPayment")
        found = [self.ix.nodes[f]["path"] for f, _ in res["files"]]
        self.assertIn("src/services/orderService.ts", found)
        keyword = [self.ix.nodes[f]["path"] for f, _ in self.ix.keyword_search("where does a payment start?")]
        self.assertNotIn("src/services/orderService.ts", keyword)
        self.assertIn("src/content/faq.ts", keyword)

    def test_graph_beats_keyword_on_multi_hop(self):
        rows = {kind: (kw, gr) for kind, _, kw, gr in evaluate(self.ix)}
        self.assertGreater(rows["multi-hop"][1], rows["multi-hop"][0])

    def test_clean_up_merges_repeats(self):
        nodes = self.raw["nodes"][:2]
        a, b = nodes[0]["id"], nodes[1]["id"]
        raw = [(a, b, "call"), (a, b, "call"), (a, a, "call"), (a, "missing", "call")]
        self.assertEqual(G.audit(nodes, raw)["repeated_links"], 1)
        self.assertEqual(G.restructure(nodes, raw), [(a, b, "call", 2)])

    def test_changed_together_from_git(self):
        with tempfile.TemporaryDirectory() as d:
            git = lambda *args: subprocess.run(
                ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                check=True, capture_output=True)
            git("init", "-q")
            for n in range(3):
                for name in ("a.js", "b.js"):
                    with open(os.path.join(d, name), "w") as f:
                        f.write(f"export const v = {n}\n")
                git("add", "-A")
                git("commit", "-q", "-m", f"change {n}")
            raw = scan(d, log=quiet)
            self.assertEqual(raw["edges"].count(("file:a.js", "file:b.js", "co_edit")), 3)

    def test_build_command_writes_the_map(self):
        with tempfile.TemporaryDirectory() as out:
            with open(os.devnull, "w") as null:
                old, sys.stdout = sys.stdout, null
                try:
                    main(["graph", "build", "--out", out, "--no-git"])
                finally:
                    sys.stdout = old
            for name in ("graph.html", "graph.json", "graph.db", "nodes.csv", "edges.csv"):
                self.assertTrue(os.path.getsize(os.path.join(out, name)) > 0, name)
            with open(os.path.join(out, "graph.html"), encoding="utf-8") as f:
                self.assertNotIn("__GRAPH_JSON__", f.read())


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = S.Box(os.path.join(self.tmp.name, "skillbox"))
        self.box.seed(S.SAMPLE_SKILLS, S.SAMPLE_TRACES)
        self.episodes = S.load_traces(S.SAMPLE_TRACES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_the_repeated_failures(self):
        lessons = {l["skill"]: l for l in S.find_lessons(self.episodes)}
        self.assertEqual(lessons["write-a-story"]["fix"], "import { fn } from 'storybook/test'")
        self.assertEqual(lessons["write-a-story"]["runs"], ["run-101", "run-104", "run-109"])
        self.assertEqual(lessons["run-the-tests"]["fix"], "pnpm vitest run")

    def test_one_failure_is_not_a_pattern(self):
        self.assertEqual(S.find_lessons(self.episodes[:1]), [])

    def test_draft_waits_for_review(self):
        pending = S.induce(self.box, self.episodes, only="write-a-story")
        self.assertEqual((pending[0]["from"], pending[0]["version"]), (1, 2))
        self.assertIn("from 'storybook/test'", pending[0]["body"])
        self.assertNotIn("from '@storybook/test'` so", pending[0]["body"])
        self.assertEqual(self.box.state["active"]["write-a-story"], 1)  # still v1 until approved
        self.assertEqual(S.Box(self.box.folder).state["pending"][0]["name"], "write-a-story")  # survives a restart

    def test_search_picks_the_right_skill(self):
        self.assertEqual(self.box.search("Add a Storybook story for the ActionButton component")[0]["name"], "write-a-story")
        self.assertEqual(self.box.search("do the unit tests pass?")[0]["name"], "run-the-tests")


class BenchTest(unittest.TestCase):
    def test_runs_both_arms_and_leaves_the_repo_alone(self):
        from atl import bench as B
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            git = lambda *args: subprocess.run(
                ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                check=True, capture_output=True)
            git("init", "-q")
            with open(os.path.join(d, "a.js"), "w") as f:
                f.write("export function startPayment() { return 1 }\n")
            git("add", "-A")
            git("commit", "-q", "-m", "first")
            tasks = os.path.join(out, "tasks.json")
            with open(tasks, "w") as f:
                f.write('[{"id": "t1", "prompt": "change how we start payment", "expect_files": ["a.js"]}]')
            agent = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_agent.py')}"
            with open(os.devnull, "w") as null:
                old, sys.stdout = sys.stdout, null
                try:
                    main(["bench", "run", "--repo", d, "--tasks", tasks, "--repeats", "2",
                          "--agent", agent, "--out", out, "--yes"])
                finally:
                    sys.stdout = old
            with open(os.path.join(out, "bench", "results.jsonl")) as f:
                rows = [__import__("json").loads(line) for line in f]
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(r["hit"] and r["changed"] == ["a.js"] for r in rows))
            s = B.summarise(rows)
            self.assertEqual(s["per_task"][0]["tool_calls"], {"plain": 6, "graph": 3, "change": -0.5})
            self.assertEqual(s["per_task"][0]["tokens"]["plain"], 6330)
            status = subprocess.run(["git", "-C", d, "status", "--porcelain"], capture_output=True, text=True)
            self.assertEqual(status.stdout, "")  # the real working copy was never edited
            trees = subprocess.run(["git", "-C", d, "worktree", "list"], capture_output=True, text=True)
            self.assertEqual(len(trees.stdout.strip().split("\n")), 1)  # temp checkouts were removed


if __name__ == "__main__":
    unittest.main()
