# Thesis Audit

## Vision

A user proposes a financial theory, and the app researches it against the Lovelace yottagraph. The response is organized into "supporting" and "contradicting" signals -- concrete evidence from the knowledge graph plus agent-synthesized analysis. The user isn't given a yes/no answer; rather a balanced collection of data and reasoning that helps them assess their original theory.

Since this is based on the Lovelace KG, this is heavily financial services focused. A high-level summary: "applying the scientific method to understanding the FSI space".

## Status

Initial implementation complete. The UI, composable, and ADK agent are built. The agent (`thesis_researcher`) needs to be deployed via `/deploy_agent` before the full workflow is functional.

## Architecture

Single-page Nuxt 3 SPA with a two-phase ADK agent workflow:

1. **Phase 1 (Clarification):** User submits thesis. Agent parses it, resolves entities against the yottagraph, and returns ranked candidates for user confirmation.
2. **Phase 2 (Research):** After user confirms entities, agent gathers evidence (news, filings, stock data, events, relationships, macro data) and organizes findings into supporting vs contradicting signals.

Both phases use the same ADK session for continuity. The frontend streams SSE events for real-time progress display.

## Modules

### `agents/thesis_researcher/`

Python ADK agent deployed to Vertex AI Agent Engine. Contains eight tools for querying the Elemental API: `lookup_entity`, `get_entity_news`, `get_stock_prices`, `get_entity_filings`, `get_entity_relationships`, `get_entity_events`, `get_macro_data`, `get_schema`. All tools return formatted strings and handle errors gracefully.

### `composables/useThesisResearch.ts`

Vue composable managing the two-phase research flow. Handles agent discovery via tenant config, SSE streaming with progress tracking, structured JSON response parsing, and session continuity between phases. Exposes reactive state for thesis, status, clarification, progress, results, and error.

### `components/`

- **ThesisInput** -- Thesis entry form with example chips and keyboard shortcut (Cmd/Ctrl+Enter).
- **EntityClarification** -- Displays resolved entity candidates with ranked selection, free-text override via "Other...", and claims list.
- **ResearchProgress** -- Real-time research step timeline derived from agent `function_call` SSE events.
- **ResearchResults** -- Two-column supporting/contradicting signal display with evidence cards and synthesis.
- **SignalCard** -- Individual evidence card with source-type chip, entity, date, and detail.

### `pages/index.vue`

Single page orchestrating five visual states: idle (input), clarifying (agent parsing), awaiting confirmation (entity review), researching (progress), and done (results). Includes inline ThesisBar sub-component for thesis display across states.
