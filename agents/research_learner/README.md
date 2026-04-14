# Research Learner

Iterative prompt optimizer for the thesis researcher's planner instruction.
Runs research against a fixed set of golden queries, scores the output with an
LLM rubric, and uses a separate LLM to generate improved prompts — in a loop,
for as long as you want.

## Setup

From the `agents/` directory:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r research_learner/requirements.txt
```

You need GCP credentials that can call Vertex AI (Gemini) and the Elemental API.
Authentication is handled by `broadchurch_auth.py` using `broadchurch.yaml` and
Application Default Credentials.

## Quick start

```bash
cd agents

# Run 10 iterations using the built-in planner prompt as the seed
python -m research_learner.run --iterations 10

# Seed with your own prompt and run for 8 hours
python -m research_learner.run --hours 8 --seed-from-file my_prompt.txt

# Run against a single query
python -m research_learner.run --iterations 5 --query apple_s_services_revenue_growth_is_offsetting_declining_ipho
```

## Watching progress

Stdout is intentionally minimal — one line per iteration:

```
learner: 4 queries, 10 iterations
log: tail -f /path/to/agents/research_learner/learner.log
  #1/10  avg=62.0  best=62.0  (1m23s)
  #2/10  avg=68.5  best=68.5  (1m15s)
```

For full detail (every API call, LLM invocation, retry, score breakdown), tail
the log:

```bash
tail -f agents/research_learner/learner.log
```

## CLI reference

### Run the learner

```
python -m research_learner.run [options]
```

| Flag                          | Description                                                |
| ----------------------------- | ---------------------------------------------------------- |
| `--iterations N`              | Max number of learner iterations                           |
| `--hours H`                   | Max wall-clock hours (can combine with `--iterations`)     |
| `--seed-from-file PATH`       | Text file with an initial planner prompt                   |
| `--force-seed`                | Insert the seed even if the DB already has prompts         |
| `--query KEY`                 | Run against a single query key instead of the full set     |
| `--parallel N`                | Thread pool size for parallel query execution (default: 4) |
| `--cooldown SECS`             | Pause between iterations (default: 2.0)                    |
| `--max-research-iterations N` | Max planner loop iterations per research run (default: 5)  |
| `--db PATH`                   | Path to SQLite database (default: `learner.db`)            |

You must specify at least one of `--iterations` or `--hours`.

### Inspect results

```bash
# Score history table
python -m research_learner.run --history

# Generate an interactive HTML report (Chart.js graphs)
python -m research_learner.run --report
# -> opens agents/research_learner/report.html

# Export the best-scoring prompt to the researcher agent
python -m research_learner.run --export-best
# -> writes agents/researcher/planner_prompt.json
```

## How it works

```
┌─────────────────────────────────────────────────────────┐
│  Learner loop (learner.py)                              │
│                                                         │
│  for each iteration:                                    │
│    1. Load current best prompt from DB                  │
│    2. Run research on all golden queries (parallel)     │
│    3. Score each research output (LLM rubric)           │
│    4. Record scores in SQLite                           │
│    5. Ask learner LLM to generate an improved prompt    │
│    6. Insert new prompt, repeat                         │
└─────────────────────────────────────────────────────────┘
```

### Scoring rubric (0–100, weighted)

Each dimension is scored 0–25 by the scorer LLM, then combined into a weighted
total:

| Dimension          | Weight | Max | What it measures                                       |
| ------------------ | ------ | --- | ------------------------------------------------------ |
| **Coverage**       | 0.20   | 20  | Did research fetch data for every entity in the query? |
| **Breadth**        | 0.20   | 20  | Were all data types from `data_needs` addressed?       |
| **Addressability** | 0.50   | 50  | Does the data actually help evaluate each claim?       |
| **Efficiency**     | 0.10   | 10  | Was research done without duplicate or wasted calls?   |

Addressability dominates — research that doesn't help evaluate the thesis
claims scores poorly regardless of how many API calls it makes.

### Prompt evolution

The learner LLM makes incremental edits to the planner instruction — typically
1–2 targeted changes per iteration, focused on the dimension where improvement
would most increase the weighted total (accounting for both headroom and weight).
If scores plateau for 3+ iterations, it tries a more creative change.

Every prompt is stored with its parent ID and generation number, so you can
trace the full lineage from seed to best.

## Golden queries

Four pre-built thesis queries in `fixtures.py`:

- Netflix vs Disney+ streaming market share
- Interest rates impact on commercial real estate
- JPMorgan vs Goldman Sachs recession positioning
- Apple services revenue vs iPhone sales

To rebuild fixtures with fresh entity NEIDs (requires Elemental API access):

```bash
python -m research_learner.build_fixtures
```

## Files

| File                | Purpose                                                 |
| ------------------- | ------------------------------------------------------- |
| `run.py`            | CLI entry point and command dispatch                    |
| `learner.py`        | Main optimization loop                                  |
| `runner.py`         | Executes planner-executor research with a custom prompt |
| `scorer.py`         | LLM-based research quality scoring                      |
| `db.py`             | SQLite schema and data access                           |
| `fixtures.py`       | Golden query set (generated by `build_fixtures.py`)     |
| `build_fixtures.py` | Resolves entity names to NEIDs via Elemental API        |
| `report.py`         | Generates self-contained HTML report with Chart.js      |
| `log.py`            | Logging setup (file logger for `learner.log`)           |
| `learner.db`        | SQLite database (created on first run, gitignored)      |
| `learner.log`       | Detailed log file (gitignored)                          |
| `report.html`       | Generated report (gitignored)                           |

## Resumability

The learner is fully resumable. If interrupted (Ctrl-C, crash, timeout), just
run the same command again — it reads the iteration count from the DB and picks
up where it left off. The `--force-seed` flag lets you inject a new seed prompt
into an existing DB.

## Publishing the best prompt

`--export-best` writes the highest-scoring prompt to
`agents/researcher/planner_prompt.json`. The production researcher agent loads
this file at runtime (falling back to `DEFAULT_OPTIMIZABLE_PROMPT` if the file
doesn't exist).
