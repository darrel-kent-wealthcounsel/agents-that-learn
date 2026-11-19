# Presenting the demos

About five minutes for both. Run everything once beforehand so nothing is new on the day.

## Before the room fills

```bash
python3 run.py graph build --repo ~/code/my-repo --question "where does a payment start?"
python3 run.py graph open        # leave this browser tab open
python3 run.py skills reset      # puts the skill box back to version 1
clear
```

Make the terminal font large. Check the question in the map gives the answer you
expect; if the anchor is wrong, reword the question with words that appear in the
function's name.

## Code graph (about 2 minutes)

| Do | Say |
| --- | --- |
| Show the browser tab | "This is our repo. Squares are files, lines are imports and calls. It took a couple of seconds to build." Read the counts under the title |
| Click the starter question | "Step one, the anchor: the best name match for the question" |
| Point at the ranked list | "Step two, the walk: files ranked by how close they are to the anchor. Each one shows the links that led to it" |
| Point at an orange line | "This file is flagged because keyword search does not return it. It never mentions the words in the question; it is only connected" |
| Open "What keyword search returns" | "Keyword search finds files that talk about the topic. The graph finds files that take part in it" |
| Click a file on the map | Optional: "Click any file to see what it links to" |

## Skill update (about 2 minutes)

```bash
python3 run.py skills demo
```

Press Enter to move between steps.

| Step on screen | Say |
| --- | --- |
| Traces | "Seven past runs. Three hit the same error and each found the same fix, at a cost of twelve extra tool calls" |
| Before | "This is the skill the agent reads for that task. Step 2 is the wrong line" |
| Draft | "A draft of version 2, built from those three runs. It is not active yet" |
| Review | "Red is today, green is the proposal, and it lists its evidence." Type `a`, then a reason |
| After | "Same task, same agent. It now reads version 2 and starts from the fix" |

To show a rejection instead, type `r` and a reason. The skill stays at version 1 and
the reason is logged.

## If something goes wrong

- The map is blank: rebuild, then reload the tab.
- No "changed together" links: the folder has no git history. Point `--repo` at a real clone.
- You need a clean start: `python3 run.py skills reset`.
