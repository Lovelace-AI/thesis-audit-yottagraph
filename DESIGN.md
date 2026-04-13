# Thesis Audit

## Vision

A user proposes a financial theory, and the app researches it against the Lovelace Knowledge Graph. The response is organized into "supporting" and "contradicting" arguments — concrete evidence from the knowledge graph plus agent-synthesized analysis. The user isn't given a yes/no answer; rather a balanced collection of data and reasoning that helps them assess their original theory.

Since this is based on the Lovelace KG, this is heavily financial services focused. A high-level summary: "applying the scientific method to understanding the FSI space".

## Status

Multi-agent pipeline is deployed and functional. Three ADK agents (`query_rewrite`, `researcher`, `report`) are deployed to Vertex AI Agent Engine. The app is live at the tenant's Vercel URL.

### Known limitations

- **OHLCV stock prices**: Financial instrument entities for major stocks are mega-entities that time out on property fetch. The researcher has MCP fallback code, but the service account currently lacks ID token minting permissions for the Lovelace MCP servers. As a result, `get_stock_prices` returns financial fundamentals (revenue, net income, etc.) rather than daily price history.
- **News coverage**: Some entities (e.g., Disney) return zero news articles, likely due to entity resolution landing on a different NEID than the one linked to news data.

## Architecture

Single-page Nuxt 3 SPA with a three-stage multi-agent pipeline:

### Stage 1: Query Rewrite (iterative loop)

1. User submits plaintext thesis.
2. **Query Rewrite Agent** (ADK, no tools) extracts entity substrings, claims, and data needs.
3. **Entity Resolution** (code) calls `searchEntities()` for each candidate, attaching ranked results with real NEIDs.
4. **User Confirmation** — user picks from ranked candidates or provides free-text corrections.
5. If any entities remain unresolved, the loop repeats: agent sees the corrections and produces new candidates.
6. Once all entities are resolved, the finalized QueryRewrite JSON proceeds to Stage 2.

The QueryRewrite is a living JSON document that accumulates state across rounds. The agent never sees or produces NEIDs — only text substrings.

### Stage 2: Research (planner-executor loop)

The researcher uses a **planner-executor architecture**:

1. An outer ADK wrapper agent exposes a single tool: `research_iteration`.
2. Each iteration, an **inner Gemini planner** (direct `google.genai` call, not ADK) examines the growing research document and responds with a batch of API calls to make, or `action: "done"`.
3. The requested calls are **mechanically executed** by Python dispatcher functions — no LLM involved in execution.
4. Results are recorded two ways: **abridged summaries** go into the research doc (for the planner's limited context window), while **full unabridged data** is kept in `_full_results` for the UI's "Show Your Work" section.
5. The loop repeats until the planner says "done" or `max_iterations` is reached.

**Available API calls** (dispatched by `_dispatch_call`, not ADK tools):
`get_news`, `get_stock_prices`, `get_filings`, `get_events`, `get_relationships`, `get_entity_properties`.

**Context management**: The research doc is abridged to stay within ~100k total, with each individual call result limited to ~10k. Full results are preserved separately.

**Iteration limit**: Configurable via Settings dialog (default 5, max 20). Passed from the frontend as `max_iterations` in the research input JSON.

### Stage 3: Report

**Report Agent** (ADK, no tools) receives `{ query, calls }` where `calls` is the array of research call results with summaries. It produces three plaintext analysis fields: `supporting_argument`, `contradicting_argument`, and `final_analysis`. The raw data (including `show_your_work`) travels through to the UI alongside the analyses.

### Design Principle

**LLMs decide _what_ to do. Code decides _what the data is_.** No agent ever writes data into the result. Raw data is mechanically stored by code; agents can only observe summaries and request more.

## Modules

### `agents/query_rewrite/`

Python ADK agent (no tools). Takes a QueryRewrite JSON, returns candidate entity substrings, claims, and data needs. Supports multi-round sessions for the iterative entity resolution loop.

### `agents/researcher/`

Python ADK agent implementing the planner-executor loop:

- **Outer wrapper**: ADK agent with one tool (`research_iteration`), using `WRAPPER_INSTRUCTION` that tells it to call the tool repeatedly until `status: "done"`.
- **Inner planner**: Direct Gemini call via `google.genai.Client` with a detailed `PLANNER_INSTRUCTION` system prompt describing all 6 available API calls, their parameters, and research strategy.
- **Executors**: `_exec_get_news`, `_exec_get_stock_prices`, `_exec_get_filings`, `_exec_get_events`, `_exec_get_relationships`, `_exec_get_entity_properties` — each returns `(summary, full_data)`.
- **MCP fallback**: `_fetch_stock_prices_mcp` attempts to call the Lovelace stocks and elemental MCP servers for OHLCV data when the REST API times out on mega financial instrument entities.
- **Data**: All calls go through `broadchurch_auth.elemental_client` for Elemental API access.

### `agents/report/`

Python ADK agent (no tools). Takes `{ query, calls }` and produces three analysis fields. Pure reasoning — no data fabrication.

### `composables/useThesisResearch.ts`

Vue composable orchestrating the three-stage pipeline. Key responsibilities:

- Agent discovery via tenant config (`/api/config/{orgId}`)
- Per-agent session management
- SSE streaming with `unwrapFunctionResponse` to handle ADK's `{result: "..."}` wrapper
- Progress tracking: `research_iteration` function_call/response events → `ResearchIteration[]`
- Fallback data reconstruction from progress if the "done" event is lost
- Error capture with `ErrorDetail` for the debug panel

Exported types: `EntityCandidate`, `QueryEntity`, `QueryRewrite`, `ReportResult`, `ResearchCallResult`, `ResearchIteration`, `EntitySelection`, `ResearchStatus`, `ErrorDetail`.

### `composables/useResearchSettings.ts`

Stores `maxIterations` (default 5) in `localStorage`. Exposed via `useResearchSettings()`.

### `components/`

- **ThesisInput** — Thesis entry form with example chips and keyboard shortcut.
- **EntityClarification** — Displays entity candidates from the QueryRewrite JSON. Shows resolved entities, pending candidates with ranked selection, free-text "Other..." option, claims, and data needs.
- **ResearchProgress** — Live iteration timeline: expandable rows per iteration showing planner reasoning and per-call labels with status icons.
- **ResearchResults** — Report display with supporting/contradicting argument cards, final analysis, entity chips (clickable for inspection), collapsible Research Data (call summaries), Show Your Work (full unabridged data per call ID), and Research Iterations replay.
- **EntityInfoCard** — Raw entity data display triggered by clicking any NEID.
- **SettingsDialog** — Configurable max research iterations (1–20) and read-only server configuration display.

### `pages/index.vue`

Single page orchestrating seven visual states: idle (input), parsing (agent analyzing), resolving (entity search), awaiting confirmation (entity review), researching/reporting (progress), done (report), and error (with expandable debug panel showing raw request/response details). Includes inline ThesisBar sub-component for thesis display across states.
