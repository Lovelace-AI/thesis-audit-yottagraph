# Thesis Audit

## Vision

A user proposes a financial theory, and the app researches it against the Lovelace yottagraph. The response is organized into "supporting" and "contradicting" arguments -- concrete evidence from the knowledge graph plus agent-synthesized analysis. The user isn't given a yes/no answer; rather a balanced collection of data and reasoning that helps them assess their original theory.

Since this is based on the Lovelace KG, this is heavily financial services focused. A high-level summary: "applying the scientific method to understanding the FSI space".

## Status

Multi-agent pipeline implemented. Three ADK agents (`query_rewrite`, `researcher`, `report`) need to be deployed via `/deploy_agent` before the full workflow is functional.

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

### Stage 2: Research

**Research Agent** (ADK, with tools) decides what data to fetch based on the finalized QueryRewrite. Each Python tool calls the Elemental API directly, appends raw results to an in-memory accumulator, and returns a read-only summary to the agent. The agent iterates until satisfied, then calls `finalize_research()` which returns the full accumulated JSON via the SSE stream.

### Stage 3: Report

**Report Agent** (ADK, no tools) receives the complete research JSON (query + raw entity data + macro data). It produces three plaintext analysis fields: `supporting_argument`, `contradicting_argument`, and `final_analysis`. The raw data travels through to the UI alongside the analyses.

### Design Principle

**LLMs decide _what_ to do. Code decides _what the data is_.** No agent ever writes data into the result. Raw data is mechanically stored by code; agents can only observe summaries and request more.

## Modules

### `agents/query_rewrite/`

Python ADK agent (no tools). Takes a QueryRewrite JSON, returns candidate entity substrings, claims, and data needs. Supports multi-round sessions for the iterative entity resolution loop.

### `agents/researcher/`

Python ADK agent with research tools: `get_news`, `get_stock_prices`, `get_filings`, `get_events`, `get_relationships`, `get_macro`, `get_entity_properties`, `finalize_research`. Each tool calls the Elemental API via `broadchurch_auth.elemental_client`, accumulates raw results in Python memory, and returns summaries.

### `agents/report/`

Python ADK agent (no tools). Takes the full research JSON and produces three analysis fields. Pure reasoning — no data fabrication.

### `composables/useThesisResearch.ts`

Vue composable orchestrating the three-stage pipeline. Manages per-agent session IDs, agent discovery via tenant config, SSE streaming with progress tracking, entity resolution via `searchEntities()`, and JSON response parsing. Exposes reactive state for thesis, status, queryRewrite, progress, report, and error.

### `components/`

- **ThesisInput** -- Thesis entry form with example chips and keyboard shortcut.
- **EntityClarification** -- Displays entity candidates from the QueryRewrite JSON. Shows resolved entities, pending candidates with ranked selection, free-text "Other..." option, claims, and data needs.
- **ResearchProgress** -- Real-time research step timeline derived from agent `function_call` SSE events.
- **ResearchResults** -- Report display with supporting/contradicting argument cards, final analysis, expandable per-entity raw data sections, macro data, and "Show Your Work" tool call trail.
- **EntityInfoCard** -- Raw entity data display triggered by clicking any NEID.

### `pages/index.vue`

Single page orchestrating six visual states: idle (input), parsing (agent analyzing), resolving (entity search), awaiting confirmation (entity review), researching/reporting (progress), and done (report). Includes inline ThesisBar sub-component for thesis display across states.
