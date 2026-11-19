# Agents that learn

Two ways an AI coding agent can get better without retraining the model, as small
demos you can run on your own machine and point at your own repo.

| Demo | Idea | Command |
| --- | --- | --- |
| Code graph | Map how the code connects, then find files by following links, not only by matching words | `python3 run.py graph build` |
| Skill update | Turn a failure that repeats across past runs into a better written skill, approved by a person | `python3 run.py skills demo` |

It needs Python 3.9 or later and nothing else. There is no install step, no database
and no network call.

The ideas come from the DeepLearning.AI course *Building Adaptive AI Agents*
(lesson 2, skill induction, and lesson 4, the code knowledge graph). The course
notebooks depend on a hosted lab and an Oracle database; this repo rebuilds the
same steps with the standard library so they run anywhere.

## Quick start

```bash
python3 run.py graph build                       # maps the bundled sample app
python3 run.py graph open                        # opens out/graph.html
python3 run.py graph ask "where does a payment start?"
python3 run.py skills demo
python3 -m unittest discover tests               # 15 checks, about a second
```

## Demo 1: the code graph

Point it at any folder of TypeScript, JavaScript, Vue or Python:

```bash
python3 run.py graph build --repo ~/code/my-repo --question "where does a payment start?"
python3 run.py graph open
```

`build` does what the lesson 4 notebook does, in order:

1. **Build.** Every file and function becomes a node. Links come from import
   statements, from function bodies (calls), from `git log` (files that change
   together) and from matching an HTTP method and path on both sides of an API call.
2. **Audit and clean.** It prints a before-and-after scorecard, merging repeated
   links and dropping broken ones.
3. **Store.** Two tables, `nodes` and `edges`, written as CSV and as a SQLite file.
4. **Draw.** `out/graph.html` is one self-contained page: the file map, a question
   box, and the ranked answer.

`ask` and the question box in the map run the two-step search:

1. **Anchor.** Pick the file or function whose name best matches the question.
2. **Walk.** Score everything near the anchor with personalised PageRank: a random
   walk along the links that keeps jumping back to the anchor.

Both show keyword search beside it, so you can see which files only the graph finds.
`python3 run.py graph eval` compares the two on generated questions.

You can query the tables directly. Files that changed together with one file:

```bash
sqlite3 out/graph.db "SELECT dst, count FROM edges WHERE kind='co_edit' AND src='file:src/stores/orderActions.ts'"
```

## Demo 2: the skill update loop

```bash
python3 run.py skills demo
```

It walks through the lesson 2 loop, pausing at each step:

1. **Traces.** Seven past runs. Three hit the same build error and found the same fix.
2. **Before.** The skill the agent picks for the task today, with the wrong line in it.
3. **Draft.** A proposed version 2, with the runs it is based on.
4. **Review.** You see the difference and approve or reject with a reason. Nothing
   changes for the agent until you approve. The draft is saved first, so you can
   close the terminal and review later with `python3 run.py skills review`.
5. **After.** The same task now picks version 2.

Other commands: `skills list`, `skills traces`, `skills search "<task>"`,
`skills induce`, `skills review`, `skills contract`, `skills reset`.

To try your own material, start from a folder of skill files and a trace file:

```bash
python3 run.py skills demo --skills ~/code/my-repo/.claude/skills --traces my-traces.jsonl
```

Your skills folder is only read. Versions and the review log live in `out/skillbox/`.
`sample/traces.jsonl` shows the trace format: one run per line, with the skill used
and each tool call marked as passed or failed.

## Measuring it: does the map help an agent?

`bench` runs real tasks through Claude Code twice, once as written and once with
the map's ranked file list added to the prompt, and compares tool calls, tokens,
time to first edit, time on task and cost. This is the same comparison the
course reports for HTTPie and Django.

```bash
python3 run.py bench run --repo ~/code/my-repo --tasks my-tasks.json --dry-run   # plan only
python3 run.py bench run --repo ~/code/my-repo --tasks my-tasks.json             # 3 runs per task per arm
python3 run.py bench report
```

- **Tasks** are a JSON list; see `sample/bench-tasks.example.json`. Each has an
  `id`, a `prompt`, and optionally `expect_files` (the files a correct change
  touches) and `question` (words for the map to search with, if the prompt is long).
- **Your working copy is never touched.** Every run happens in a temporary git
  worktree at one commit, which is deleted afterwards.
- **The agent can read, search and edit, and nothing else.** The default command is
  `claude -p --output-format stream-json --verbose --permission-mode acceptEdits
  --allowedTools Read,Grep,Glob,Edit,Write --max-turns 40`. Change it with `--agent`.
- **It resumes.** Stop it at any time and run the same command to carry on.
  `--fresh` starts over.
- **Each run uses your Claude Code account**, so 6 tasks at 3 repeats is 36 agent runs.

Read the result with care. Agent runs vary, so use at least 5 tasks and 3 repeats,
choose the tasks before you look at any results, and report the tasks where the
map did not help alongside the ones where it did. Before a full run, do a
`--repeats 1` run on one task and check `out/bench/logs/`.

## What is different from the course

| Course notebook | Here |
| --- | --- |
| Oracle property graph and `MATCH` queries | CSV and SQLite tables; search runs in memory |
| Anchor chosen by an embedding model (by meaning) | Anchor chosen by words in names, comments and file contents. A question must share words with the code |
| Calls read by the course's parser | JS, TS and Vue are read by pattern, so some calls are missed or wrong. Python uses the `ast` module |
| Retrieval traces folded into the graph | Not included |
| An LLM drafts the skill, replayed from saved output | A rule-based reader drafts it, so the result is the same every run. `--live` asks a model instead |
| The agent is re-run to confirm the fix | No agent runs here. The demo shows which skill the agent would read |
| Benchmarks on HTTPie and Django | `bench` runs the same comparison on your repo. `graph eval` uses questions generated from the graph, so it compares search methods and says nothing about real tasks |

`skills induce --live` needs `ANTHROPIC_API_KEY` and `ATL_MODEL` (a model name) in
your environment. It has not been tested against the live API.

The sample app in `sample/shop` and the traces in `sample/traces.jsonl` are made up
for the demo.

## Before you share

A graph built from a private repo holds that repo's file names, function names and
word counts, and bench logs hold the agent's full transcript of working in it,
including local paths. Treat both like the source code they came from.
`.gitignore` keeps `out/`, any `out-*/` folder and task files named `*-tasks.json`
out of version control. Keep your own task lists under one of those names, or
outside this repo.

This repo's own code, sample app and traces contain nothing private.

## Layout

```
run.py              entry point
atl/scan.py         read a repo into nodes and links
atl/graph.py        audit, clean, store
atl/retrieve.py     anchor, PageRank walk, keyword search, comparison
atl/viewer.html     the map page (the search is repeated here in JavaScript)
atl/skills.py       traces, draft, review, skill box
atl/bench.py        run an agent with and without the map and compare
sample/             a small made-up app, five skills and seven traces
tests/              python3 -m unittest discover tests
DEMO.md             a five-minute script for presenting both demos
```
