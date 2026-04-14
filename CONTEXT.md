# Agent Overview — Thesis Audit

Detailed context dump for AI agents working on this project. Read this before
making changes. See also `DESIGN.md` for the shorter architectural summary.

## What this app does

A user types a financial theory ("When Netflix stock rises, Disney and Paramount
stocks tend to fall within 3 months"). The app researches it against the Lovelace
Knowledge Graph and returns a balanced report: supporting evidence, contradicting
evidence, and a final analysis. The user is never given a yes/no — they get raw
data and structured reasoning to assess the theory themselves.

## System architecture

Single-page Nuxt 3 SPA with a three-stage pipeline powered by ADK agents
deployed to Vertex AI Agent Engine.

```
User thesis (plaintext)
  │
  ▼
┌─────────────────────────────────┐
│  Stage 1: Query Rewrite         │  ADK agent (no tools)
│  - Extract entities, claims     │  gemini-2.5-flash-lite
│  - Entity resolution via code   │
│  - User confirms/corrects       │
│  - Loop until all resolved      │
└────────────┬────────────────────┘
             │ QueryRewrite JSON
             ▼
┌─────────────────────────────────┐
│  Stage 2: Research              │  ADK wrapper (gemini-2.5-flash-lite)
│  ┌────────────────────────────┐ │    calls research_iteration() in loop
│  │ Inner planner (2.5-flash-  │ │
│  │ lite) decides API calls    │ │  Direct google.genai call, NOT ADK
│  │         │                  │ │
│  │         ▼                  │ │
│  │ Python executors fetch     │ │  No LLM — mechanical code
│  │ data from Elemental API    │ │
│  │         │                  │ │
│  │         ▼                  │ │
│  │ Results abridged → doc     │ │  Full results → _full_results
│  │ Loop until "done" or limit │ │
│  └────────────────────────────┘ │
└────────────┬────────────────────┘
             │ { query, calls, show_your_work }
             ▼
┌─────────────────────────────────┐
│  Stage 3: Report                │  ADK agent (no tools)
│  - Receives { query, calls }    │  gemini-2.5-flash-lite
│  - Produces supporting_argument │
│  - Produces contradicting_arg   │
│  - Produces final_analysis      │
└─────────────────────────────────┘
```

## Models in use (as of April 2026)

| Component                    | Model                   | Why                                                    |
| ---------------------------- | ----------------------- | ------------------------------------------------------ |
| Query Rewrite agent          | `gemini-2.5-flash-lite` | Simple extraction task, cheapest option                |
| Researcher wrapper agent     | `gemini-2.5-flash-lite` | Trivial role: just calls research_iteration repeatedly |
| **Research planner** (inner) | `gemini-2.5-flash-lite` | Plans API calls each iteration; direct genai call      |
| Report agent                 | `gemini-2.5-flash-lite` | Reasoning over structured data                         |
| Learner planner (local)      | `gemini-2.5-flash-lite` | Same role as research planner, for learner runs        |
| Learner scorer (local)       | `gemini-2.5-flash-lite` | Grades research quality                                |
| Learner optimizer (local)    | `gemini-2.5-flash`      | Generates improved planner instructions                |

The planner was on `gemini-2.5-pro` briefly but moved to flash-lite for performance.
`gemini-3.1-flash` / `gemini-3.1-flash-lite` were attempted but returned 404 —
those models are not yet available on Vertex AI. Gemini 2.5 Pro models tend to
prefix reasoning with dramatic language like "CRITICAL:" — that's the model's
style, not a bug.

### Cost profile

This is currently based on estimations; not proven.

A typical query (5 research iterations) costs roughly **2–3 cents**. A heavy
session (20 iterations, large context) might reach **10–20 cents**. The
planner is the dominant cost driver due to its per-iteration context size.

The research learner running 10 hours costs approximately **$3–5** on flash-lite.

## The planner-executor loop (core architecture)

This is the most important thing to understand. The researcher agent does NOT
use ADK tool-calling to fetch data. Instead:

1. **One ADK tool**: `research_iteration`. The outer wrapper agent calls it
   repeatedly.
2. **Inner planner**: A direct `google.genai.Client.models.generate_content()`
   call with `PLANNER_INSTRUCTION` as system prompt. It receives an abridged
   JSON research document and returns either `{"action": "research", "calls": [...]}`
   or `{"action": "done", "reasoning": "..."}`.
3. **Mechanical execution**: `_dispatch_call()` routes each call spec to a
   Python executor function. No LLM involved. The executor calls the Elemental
   API, parses the response, and returns `(summary_string, full_data_dict)`.
4. **Dual storage**: The summary goes into `_research_doc["calls"]` (abridged,
   for the planner's context). The full data goes into `_full_results[call_id]`
   (for the UI's "Show Your Work" section).
5. **Loop**: Repeat until the planner says "done" or `_max_iterations` is hit.

### Why this design?

The principle: **LLMs decide _what_ to do. Code decides _what the data is_.**
No LLM ever writes or fabricates data. Raw data is mechanically stored by code;
agents can only observe summaries and request more.

## The planner instruction (two-part structure)

The planner's system prompt is assembled from two pieces:

1. **PREAMBLE** (`planner_prompt.py`): Fixed mechanics — role description, JSON
   response format, API call signatures and parameter documentation. Never
   modified by the learner.

2. **Optimizable artifact**: A JSON dict with 6 keys:
    - `strategy` — high-level research approach (how to map data_needs to calls,
      when to stop, etc.)
    - `skill_filings` — how SEC filings work in the KG
    - `skill_fundamentals` — how XBRL/us_gaap data works
    - `skill_market` — how OHLCV stock data works (lives on financial_instrument
      entities, not organizations)
    - `skill_news` — how news data works
    - `skill_discovery` — entity resolution and relationship navigation

    The learner optimizes this artifact iteratively. The default lives in
    `DEFAULT_OPTIMIZABLE_PROMPT`. A learned version can be saved as
    `planner_prompt.json` and loaded at runtime.

## Available research API calls

These are NOT ADK tools. They are Python functions dispatched by `_dispatch_call`:

| Call type           | Required params                     | Optional params                    | What it does                            |
| ------------------- | ----------------------------------- | ---------------------------------- | --------------------------------------- |
| `search_entities`   | `query`                             | `flavors`, `max_results`           | Name-based entity search, returns NEIDs |
| `get_properties`    | `entity_name`, `neid`, `properties` | `limit` (default 10)               | Fetch specific named properties by name |
| `get_news`          | `entity_name`, `neid`               | `limit` (default 15)               | News articles linked to entity          |
| `get_filings`       | `entity_name`, `neid`               | `form_types`, `limit` (default 20) | SEC filings (Form 4, 8-K, 13D, etc.)    |
| `get_events`        | `entity_name`, `neid`               | `limit` (default 20)               | SEC 8-K corporate events                |
| `get_relationships` | `entity_name`, `neid`               | `direction`, `limit` (default 20)  | Linked entities (subsidiaries, etc.)    |

The optional params are the result of a fine-grained API refactor — they give the
planner explicit control over data volume and cost. `_dispatch_call` uses
`inspect.signature()` to filter incoming params to only those accepted by each
executor, so the planner can freely pass optional params without crashing if an
executor doesn't support one.

All go through `broadchurch_auth.elemental_client` for Elemental API access.
The client uses a persistent `httpx.Client` for TCP/TLS connection reuse across
calls within a session.

### `get_properties` requires explicit property names

`get_properties` **must** be called with a non-empty `properties` list. Calling
it without properties (or with an empty list) returns an error. This was
hardened to prevent "fetch all properties" calls that time out on mega-entities.
The planner instruction documents this requirement.

## Context window management

The research doc is abridged before each planner call:

- **`_MAX_DOC_SIZE = 500_000`** characters total budget
- **`_MAX_PER_RESULT = 20_000`** characters per individual call result
- Per-result budget shrinks dynamically: `available / len(calls)`
- Abridging is mechanical: long strings truncated, long lists trimmed with
  "... and N more items", dicts recursively trimmed per key
- The query portion is preserved in full; only call results are abridged

Full unabridged results are kept in `_full_results` (Python dict, keyed by
call ID) and delivered to the frontend in `show_your_work`.

## Research iteration limit

- **Default**: 20 iterations (`DEFAULT_MAX_ITERATIONS` in `agent.py`)
- **Configurable**: Settings dialog in the UI (1–20 range), stored in localStorage
- **Passed as**: `max_iterations` in the research input JSON from frontend
- **Enforced**: If `_iteration_counter > _max_iterations`, the loop stops and
  returns whatever data has been accumulated

## Known data access issues

### NEID zero-padding (fixed April 2026)

The `/elemental/find` endpoint requires `to_entity` to be a **20-character
zero-padded string**. NEIDs returned by `entities/search` can be 19 characters
(or fewer). The researcher agent's executors were passing these through
unpadded, causing **intermittent 400 Bad Request** errors — intermittent
because only some entities have short NEIDs.

**Fix**: `_dispatch_call()` now normalizes `neid` with `zfill(20)` before
passing it to any executor. The frontend `elementalHelpers.ts` has a
`padNeid()` utility for the same purpose, and `server/api/entity/[neid].get.ts`
also pads. Any new code path that passes NEIDs to the Elemental API should
always zero-pad to 20 characters.

### Error diagnostic propagation (fixed April 2026)

`httpx.HTTPStatusError` from `raise_for_status()` produces a generic message
like `Client error '400 Bad Request' for url '...'` — the response body
(which contains the actual API explanation) is on `e.response.text` but
`str(e)` discards it. The broad `except Exception as e:` blocks in every
executor were stringifying the exception, destroying the diagnostic.

**Fix**: `_error_detail()` helper in `agent.py` extracts up to 300 chars of
`e.response.text` for `HTTPStatusError` exceptions. All executor `except`
blocks now use `_error_detail(e)` instead of bare `{e}`. The error string
that travels through the ADK event stream to the frontend now includes the
API's actual error body.

Additionally, `_ElementalClient._log_request()` in `broadchurch_auth.py`
now logs both the request payload and response body at WARNING level for
any HTTP status >= 400, providing server-side visibility independent of the
user-facing error path.

### Mega-entities and OHLCV stock prices

Financial instrument entities for major stocks (Netflix, Apple, Disney) are
"mega-entities" with 20+ years of daily OHLCV data. Fetching all properties
via the REST API (`/elemental/entities/properties`) consistently times out
(502 Gateway Error after ~30s).

**Current workaround**: The planner instruction teaches the planner to call
`get_properties` with specific price property names (`close_price`,
`open_price`, etc.) and a `limit` parameter, which fetches only the requested
subset. The PID-filtered fetch keeps the response small enough to avoid
timeouts.

### 64-bit integer PID precision loss

Property IDs (PIDs) in the Knowledge Graph are 64-bit integers. When these
travel through JSON serialization between Python and JavaScript (or Python
and the Go-based gateway), they can lose precision. This breaks any API call
that filters by PID value. The workaround is to always filter by property
_name_, not PID, and do PID-to-name mapping client-side after fetching.

### Filing types in the KG

The KG does NOT store 10-K or 10-Q filing entities. Financial statement data
from those filings is denormalized into `us_gaap:*` properties directly on
organization entities. `get_filings` only returns Form 4 (insider trades),
8-K (material events), 13D/G (beneficial ownership), and 13F-HR
(institutional holdings). This is a very common source of confusion — the
planner instruction includes explicit warnings about it.

### News coverage gaps

Some entities return zero news articles. This is typically because entity
resolution landed on a different NEID (e.g., an NLP-sourced organization)
than the one the KG links news to (e.g., an EDGAR-sourced organization).
Searching for the entity by name with `search_entities` can sometimes find
a better NEID with news coverage.

### `broadchurch_auth.py` config discovery

`_load_config()` searches for `broadchurch.yaml` in three locations:

1. `Path("broadchurch.yaml")` — current working directory
2. `Path(__file__).parent / "broadchurch.yaml"` — same directory as the module
3. `Path(__file__).parent.parent / "broadchurch.yaml"` — one level up (project root)

The third path was added because when running the learner from `agents/`, both
(1) and (2) resolve to `agents/` but `broadchurch.yaml` lives in the project
root. Without it, `_uses_gateway_proxy()` returns False, the client falls back
to minting a GCP ID token (which fails or expires), and all API calls 401.

### Local dev auth: gateway proxy vs direct QS

Two auth paths exist:

| Path              | Auth mechanism     | Token lifetime          | When used                                                        |
| ----------------- | ------------------ | ----------------------- | ---------------------------------------------------------------- |
| **Gateway proxy** | `X-Api-Key` header | Never expires           | Default when `broadchurch.yaml` has `gateway.url` + `qs_api_key` |
| **Direct QS**     | `Bearer` token     | Short-lived (Auth0 JWT) | When `ELEMENTAL_API_URL` env var is set                          |

**For local dev (learner, scripts), always use the gateway proxy path.** Don't
set `ELEMENTAL_API_URL` — it overrides the proxy and forces direct QS access
with a bearer token that expires quickly. If you previously set it, `unset
ELEMENTAL_API_URL` before running.

### Logging: elemental_client noise

The `_ElementalClient` in `broadchurch_auth.py` logs every HTTP request
(method, path, status, timing). Its logger (`elemental_client`) has
`propagate = True` and no direct `StreamHandler`, so these logs are captured
by whatever root logger the caller configures. In the learner, they go to
`learner.log`. In production (ADK), they go wherever ADK routes logging.
They should never appear on stdout unless a handler is explicitly added.

## The research learner (`agents/research_learner/`)

A local-only optimization system that iteratively improves the planner
instruction. NOT deployed — runs on the developer's machine.

### Architecture

```
Learner iteration:
  ├── Run research × 4 queries in parallel
  │     └── Each: planner LLM × N iters → executor → score
  ├── Aggregate scores
  ├── Learner LLM generates improved instruction
  └── Store new instruction in SQLite DB
```

Three distinct LLM callers:

1. **Planner LLM** (`runner.py`): Called every research iteration to decide
   API calls. Same role as the deployed researcher's inner planner.
2. **Scorer LLM** (`scorer.py`): Called once per research run. Grades output
   on 4 dimensions (coverage, breadth, addressability, efficiency) with
   addressability weighted 50%.
3. **Learner LLM** (`learner.py`): Called once per learner iteration. Receives
   the current planner instruction + score history and produces an improved
   version. Makes incremental changes — 1-2 targeted edits per iteration.

### LLM call timeouts

All three LLM callers (planner, scorer, learner) use `ThreadPoolExecutor` to
enforce timeouts on `generate_content` calls that can hang indefinitely.

**Critical implementation detail**: Do NOT use `with ThreadPoolExecutor() as
pool:`. The context manager calls `pool.shutdown(wait=True)` on exit, which
blocks until the hung thread finishes — defeating the timeout entirely. Instead:

```python
pool = ThreadPoolExecutor(max_workers=1)
try:
    future = pool.submit(generate_fn)
    response = future.result(timeout=TIMEOUT)
    pool.shutdown(wait=False)
except TimeoutError:
    pool.shutdown(wait=False)  # don't wait for the hung thread
    ...
```

Timeout values: planner 120s, scorer 120s, learner 180s. Each has 3 retries
with backoff for rate-limit errors.

### Schema introspection for the learner

The learner can detect "schema misunderstandings" — patterns where the
researcher consistently fails to find data because it's looking in the wrong
place. This is implemented in `_detect_schema_misunderstandings()`.

**Schema source**: Property definitions are loaded from the data-model skill
files at `.cursor/skills/data-model/*/schema.yaml` (via
`_load_data_model_schema()`). This is preferred over the live QS `/schema`
endpoint because it includes descriptions, `domain_flavors`, display names,
and source provenance. The data is cached after first load.

The detection analyzes call traces for:

- `get_filings` repeatedly failing for 10-K/10-Q (those filing entities
  don't exist; data is in `us_gaap:*` properties on organizations)
- `get_properties` returning empty for specific property names (may be
  wrong name or wrong entity type)

When hints are detected, they're passed to the learner LLM as
`schema_hints` in its input payload, with a `LEARNER_SCHEMA_HINT_ADDENDUM`
appended to its system instruction.

### What the learner optimizes

Only the "optimizable artifact" portion of the planner instruction (strategy +
5 skill blocks). The PREAMBLE (role, format, API signatures) is fixed. The
learner outputs a new JSON artifact dict which is stored in SQLite and used
for the next iteration's research runs.

### Prompt branching (added April 2026)

The learner can "branch" from any earlier prompt in the iteration history, not
just the latest one. This creates a tree structure in the `prompts` table (the
`parent_id` column already supported this).

**How it works**: The learner LLM receives the best-ever prompt's content as
`best_prompt_json` (alongside the current prompt). If the learner decides to
branch, it sets `base_prompt_id` in its output to point at an earlier prompt.
The loop then inserts the new prompt with `parent_id` set to that earlier
prompt instead of the latest.

**When to branch**: The branching instructions (`LEARNER_BRANCH_ADDENDUM`) are
conditionally withheld from the learner LLM until the criteria are met: 3+
consecutive iterations all scoring 5+ points below the best-ever. When
criteria aren't met, the LLM literally doesn't know branching exists — this
saves tokens and prevents premature reverts. The `_should_allow_branch()`
function gates this.

The `score_history` entries sent to the learner include `parent_prompt_id` so
it can see the tree structure and understand which prompts are on which branch.

### Learner report

`report.py` generates a self-contained HTML file (`report.html`) with Chart.js
graphs. Three charts:

1. **Score Over Time**: Per-query score lines + average line across iterations.
2. **Sub-Score Breakdown**: Stacked bar chart of the 4 scoring dimensions.
3. **Prompt Evolution**: Scatter chart showing the prompt tree. Each node is an
   iteration plotted at (iteration_number, avg_score). Lines connect each node
   to its parent iteration. Branches get distinct colors. Branch points are
   visible where two lines fork from the same parent node.

The Score History table includes a "Parent" column with orange `BRANCH` tags
when a prompt's parent differs from the previous iteration's prompt (i.e., a
fork occurred).

### Scoring rubric

| Dimension          | Weight  | What it measures                            |
| ------------------ | ------- | ------------------------------------------- |
| Coverage           | 20%     | Did research cover all entities?            |
| Breadth            | 20%     | Were all relevant data types fetched?       |
| **Addressability** | **50%** | Does the data help evaluate each claim?     |
| Efficiency         | 10%     | Were calls efficient (no duplicates/waste)? |

### Running the learner

```bash
cd agents
python -m research_learner --hours 10 --max-workers 4
```

Logs to `agents/research_learner/learner.log`. Results stored in
`agents/research_learner/learner.db` (SQLite). Reports generated automatically
at end of run.

## Frontend architecture

### SSE streaming and ADK quirks

Agent communication uses Server-Sent Events. The frontend
(`useThesisResearch.ts`) processes these events and must handle ADK's wrapping
behavior:

- ADK wraps tool return values in `{"result": "JSON_STRING"}` or similar
  (`content`, `output`). The `unwrapFunctionResponse()` helper handles this.
- The `research_iteration` tool returns progress events during research and a
  final "done" event with the complete data.
- If the "done" event is lost or malformed, the frontend reconstructs partial
  data from accumulated progress events.

### Error debugging

The UI has an expandable error debug panel showing raw request/response details
for every HTTP call attempted (local proxy, portal stream, portal query). This
is populated via the `ErrorDetail` interface in `useThesisResearch.ts`.

When an agent returns an unparseable response (e.g., the LLM model is
unavailable), the raw agent response text is captured via `agentText` on the
error object and surfaced in the debug panel. SSE error events also capture
their `message`/`error` payloads into the debug request list.

### Visual states

The main page (`pages/index.vue`) has seven states:
idle → parsing → resolving → awaiting_confirmation → researching → reporting → done

If query rewrite identifies no entities, `awaiting_confirmation` is skipped
and the flow goes directly from resolving → researching.

Plus `error` (with debug panel) and entity inspection (EntityInfoCard).

### Research progress UI

During research, the UI shows an expandable timeline of iterations. Each
iteration shows the planner's reasoning and per-call labels with status icons.
This is driven by `function_call`/`function_response` SSE events for
`research_iteration`.

### Research stop reasons (added April 2026)

When the researcher finishes, the final iteration includes a `stop_reason`
field categorizing why it stopped:

| `stop_reason`    | Meaning                            |
| ---------------- | ---------------------------------- |
| `complete`       | Planner decided it has enough data |
| `max_iterations` | Hit the iteration limit            |
| `planner_error`  | Planner LLM call failed            |

The UI renders the final iteration distinctly from normal iterations: a
colored chip (green for complete, warning for limits/errors) with an icon,
and the reasoning text in a readable block instead of the tiny mono text
used for mid-loop reasoning. This is in `ResearchResults.vue` — the
`stopReason` field on `ResearchIteration` drives the conditional rendering.

The planner prompt (`planner_prompt.py`) was also strengthened: the "done"
response format and the strategy section now require the LLM to cite each
claim and whether data was found, plus any gaps and why. Without this, the
planner would often return vague "Research complete" with no explanation.

## Deployment

### Agents (Vertex AI Agent Engine)

Deployed via GitHub Actions workflow `deploy-agent.yml`. Two ways to trigger:

**Via Portal API** (preferred — no GitHub CLI needed):

```bash
curl -sf -X POST "<GATEWAY_URL>/api/projects/<ORG_ID>/deploy" \
  -H "Content-Type: application/json" \
  -d '{"type": "agent", "name": "researcher"}'
```

**Via GitHub Actions CLI**:

```bash
gh workflow run deploy-agent.yml -f agent_name=researcher
```

The workflow bundles `broadchurch.yaml` and `broadchurch_auth.py` into the
agent directory, then runs `adk deploy agent_engine`. After deploy, it
registers the new engine ID with the Portal gateway and runs a smoke test.

**Important**: Each deploy creates a NEW engine ID. The Portal gateway tracks
these, and the frontend discovers them via `/api/config/{orgId}`. Old engine
IDs become stale but are not automatically cleaned up.

### Frontend (Vercel)

Auto-deploys on push to `main`. No manual steps needed.

### What requires redeployment

| Change                   | Requires                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------- |
| Agent Python code        | Agent redeploy via workflow                                                                     |
| Planner instruction text | Agent redeploy (PREAMBLE in `planner_prompt.py`; `planner_prompt.json` also bundled if present) |
| Frontend Vue/TS code     | Push to main (Vercel auto-deploys)                                                              |
| Learner code             | Nothing (runs locally)                                                                          |
| Model change in agent    | Agent redeploy                                                                                  |
| Model change in learner  | Nothing (runs locally)                                                                          |
| Abridging limits         | Agent redeploy                                                                                  |
| Max iterations default   | Agent redeploy                                                                                  |

## File inventory

### Deployed agents (`agents/`)

| File                                  | Role                                                                                     |
| ------------------------------------- | ---------------------------------------------------------------------------------------- |
| `agents/query_rewrite/agent.py`       | Query rewrite ADK agent                                                                  |
| `agents/researcher/agent.py`          | Researcher: wrapper + planner + executors                                                |
| `agents/researcher/planner_prompt.py` | Two-part planner instruction (PREAMBLE + optimizable artifact)                           |
| `agents/report/agent.py`              | Report ADK agent                                                                         |
| `agents/broadchurch_auth.py`          | Shared Elemental API auth + httpx connection pooling (bundled into each agent at deploy) |

### Research learner (`agents/research_learner/`)

| File                    | Role                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------- |
| `run.py`                | CLI entry point (`python -m research_learner`)                                            |
| `runner.py`             | Runs planner-executor loop with swappable instruction                                     |
| `scorer.py`             | Grades research output quality                                                            |
| `learner.py`            | Outer optimization loop                                                                   |
| `db.py`                 | SQLite storage for prompts, runs, scores; tree queries for branching                      |
| `fixtures.py`           | Test queries with pre-resolved entities                                                   |
| `report.py`             | Generates HTML report with score charts and prompt evolution tree                         |
| `build_fixtures.py`     | Generates golden query fixtures with interactive entity resolution (`--interactive` flag) |
| `entity_overrides.json` | Persisted entity resolution overrides (created by `build_fixtures.py`)                    |
| `log.py`                | Logging setup                                                                             |

### Frontend

| File                                 | Role                                                     |
| ------------------------------------ | -------------------------------------------------------- |
| `composables/useThesisResearch.ts`   | Pipeline orchestration, SSE processing, state management |
| `composables/useResearchSettings.ts` | Max iterations setting (localStorage)                    |
| `pages/index.vue`                    | Main page with 7 visual states                           |
| `components/ThesisInput.vue`         | Thesis entry form                                        |
| `components/EntityClarification.vue` | Entity resolution UI                                     |
| `components/ResearchProgress.vue`    | Live iteration timeline                                  |
| `components/ResearchResults.vue`     | Report display + Show Your Work                          |
| `components/EntityInfoCard.vue`      | Entity data inspector                                    |
| `components/SettingsDialog.vue`      | Settings (max iterations, server config)                 |

## Conventions

- **No commits without explicit user request.** Make local changes only.
- **Run `npm run format` before committing.**
- **Commit message format**: `[Agent commit] {summary}`
- **Design principle**: LLMs decide what to do, code decides what the data is.
- **NEIDs are sacred**: Never fabricate them. Always from entity resolution or
  search_entities results. **Always zero-pad to 20 characters** before passing
  to the Elemental API (`zfill(20)` in Python, `padStart(20, '0')` in TS).
- **Property names over PIDs**: Always filter by property name, never by PID
  (precision loss risk).
- **Never discard HTTP error bodies**: When catching `httpx.HTTPStatusError`
  (or similar), always include `e.response.text` in the error message. The
  generic `str(e)` from HTTP libraries only shows the status code, not the
  API's explanation of what went wrong.
- **Agent error strings flow end-to-end**: Error strings from executor
  `except` blocks travel through `_dispatch_call` → call record → ADK event
  stream → gateway → frontend. Include enough context in the string for the
  user (or a debugging agent) to diagnose the problem without access to
  server logs.
