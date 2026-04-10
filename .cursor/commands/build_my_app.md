# Build My App

Read the project brief and build the described application.

## Overview

This command reads `DESIGN.md` (which contains the project creator's vision) and implements the described application using standard Nuxt patterns, Vuetify components, and the Lovelace platform's data APIs.

**This is meant to be the first thing a user runs after opening their project in Cursor.**

---

## Step 1: Read the Brief and Design References

Read `DESIGN.md` from the project root.

```bash
cat DESIGN.md
```

Look for a `## Vision` section -- this contains the project creator's description of what they want to build.

**If the file doesn't exist or has no Vision section:**

> No project brief found. That's fine -- tell me what you'd like to build and I'll help you get started!

Stop here and wait for the user to describe what they want.

### Long-form briefs

If the Vision section is long (roughly 500+ words -- a full PRD, spec, or design doc), don't try to hold it all in your head at once. Instead:

1. Read the full Vision section carefully.
2. Extract the **core purpose** (one sentence: what is this app for?).
3. Identify the **MVP feature set** -- the minimum set of features that delivers the core value. Look for explicit priority indicators (P0/P1, "must have" vs "nice to have", numbered phases). If none exist, use your judgment: what's the smallest thing that works end-to-end?
4. Create `design/requirements.md` with your extracted requirements, grouped by priority. This becomes your working checklist.
5. Build the MVP first, then iterate. Don't try to implement every detail from a long brief in one pass.

### Design references

Check for a `## Design References` section in DESIGN.md and for files in `design/references/`:

```bash
ls design/references/ 2>/dev/null
```

If design reference images exist (screenshots from Figma or other design tools):

1. **Examine each image** -- these are design mockups from the project creator showing what the app should look like.
2. Describe what you see in each image: layout structure, navigation patterns, component types, color usage, typography.
3. Map visual elements to **Vuetify components** (cards = `v-card`, data tables = `v-data-table`, navigation drawers = `v-navigation-drawer`, app bars = `v-app-bar`, etc.). If a `vuetify-figma` skill is available in `.cursor/skills/`, read it for detailed component mapping guidance.
4. Use the design references alongside the Vision text to plan the UX. The images show _what it should look like_; the Vision text explains _what it should do_.

If a Figma URL is referenced in DESIGN.md, note it for the user but don't attempt to fetch it -- work from the uploaded screenshots and the text brief.

---

## Step 2: Check MCP Servers

Check if Lovelace MCP tools are available by looking at your tool list for
tools like `elemental_get_schema`, `elemental_get_entity`, etc.

**If MCP tools are available:** Great — you have access to Lovelace platform
tools (entity search, market data, news, etc.) that you can use during
development.

**If MCP tools are NOT available:** Check if `.cursor/mcp.json` exists:

```bash
cat .cursor/mcp.json 2>/dev/null
```

If the file exists but tools aren't showing, they may need to be enabled:

> Your project has Lovelace MCP servers configured (`.cursor/mcp.json`),
> but they don't appear to be active yet. Cursor disables new MCP servers
> by default.
>
> Open **Cursor Settings** (Cmd+Shift+J) → **Tools & MCP** and enable the
> `lovelace-*` servers listed there. They should show green toggles when
> active. Let me know when they're enabled (or if you'd like to skip this).

Wait for confirmation before proceeding. If the user skips this step or
the settings panel isn't available (e.g. Cursor Cloud), proceed without
MCP tools — the app can still be built using the Elemental API client
(`useElementalClient()`) directly.

---

## Step 3: Understand the Environment

First, ensure dependencies are installed (types aren't available without this):

```bash
test -d node_modules || npm install
```

Skills in `.cursor/skills/` are populated during project init (`node init-project.js`).
If that directory is empty after `npm install`, run `node init-project.js` to install them.

Then read these files to understand what's available:

1. `DESIGN.md` -- project vision and current status
2. `broadchurch.yaml` -- project config (name, gateway URL, etc.)
3. **The `data` cursor rule** -- this is critical. It describes the Query Server, the platform's primary data source. Build against platform APIs, not external sources.
4. **`.cursor/skills/`** — Each subdirectory is one skill. List them, open each skill’s entry (usually `SKILL.md`) and follow its structure to learn what is documented (APIs, schemas, helpers, etc.).
5. `.cursor/rules/` -- scan rule names to know what other patterns are available

**Important: Use the platform's data.** This app runs on the Lovelace platform, which provides a Query Server with entities, news, filings, sentiment, relationships, events, and more. Read the `data` rule and the skills under `.cursor/skills/` to understand what data is available. Use `getSchema()` to discover entity types and properties at runtime.

Key capabilities:

- **Query Server / Elemental API** -- the primary data source. Use `useElementalClient()` from `@yottagraph-app/elemental-api/client`. See the `data` rule.
- **KV storage** -- always available for preferences and lightweight data (see `pref` rule)
- **Neon Postgres** -- check if `DATABASE_URL` is in `.env` for database access (see `server` rule)
- **AI agent chat** -- use the `useAgentChat` composable to build a chat UI for deployed agents
- **MCP servers** -- Lovelace MCP servers may be available (check `.cursor/mcp.json`)
- **Components** -- Vuetify 3 component library is available

---

## Step 4: Verify Data Availability

Before designing UX, verify that the data your app needs actually exists
in the knowledge graph. This prevents building features around empty data.

**If MCP tools are available:**

```
elemental_get_schema()                          → list entity types, confirm your target types exist
elemental_get_entity(entity="Microsoft")        → verify entity lookup works with a known entity
elemental_get_entity(entity="Apple Inc")        → try another known entity
```

If schema calls succeed but entity lookups return "not found," that means
the entity type exists in the schema but has no data. That's a **data
issue**, not a broken server. Try different, well-known entity names.

**If MCP tools are NOT available, use curl:**

```bash
# Read credentials from broadchurch.yaml
GW=$(grep 'url:' broadchurch.yaml | head -1 | sed 's/.*"\(.*\)".*/\1/')
ORG=$(grep 'org_id:' broadchurch.yaml | sed 's/.*"\(.*\)".*/\1/')
KEY=$(grep 'qs_api_key:' broadchurch.yaml | sed 's/.*"\(.*\)".*/\1/')

# List entity types
curl -s "$GW/api/qs/$ORG/elemental/metadata/schema" -H "X-Api-Key: $KEY"

# Search for a known entity
curl -s "$GW/api/qs/$ORG/entities/search" \
  -X POST -H "Content-Type: application/json" -H "X-Api-Key: $KEY" \
  -d '{"queries":[{"queryId":1,"query":"Microsoft"}],"maxResults":3,"includeNames":true}'
```

**Document what you find.** If certain entity types have sparse data, note
it in your UX plan. Design features around data that actually exists, and
mark aspirational features (that need more data) as future work.

---

## Step 5: Design the UX

Based on the brief, think about the right UX for this specific problem. Do NOT default to a sidebar-with-tabs layout. Consider:

- **Single-page app** -- if the core experience is one focused view (e.g. a dashboard, a watchlist, a chat interface)
- **Multi-page with navigation** -- if the app has distinct sections. Choose the right nav pattern: sidebar, top tabs, bottom nav, breadcrumbs, etc.
- **Hybrid** -- a primary view with secondary pages accessible from a menu or header

Design the UX around the user's workflow, not around a fixed navigation pattern.

Plan what you'll build:

1. What pages to create in `pages/`
2. What reusable components to extract into `components/`
3. What shared logic belongs in `composables/`
4. What data needs to be persisted (and whether KV or Neon Postgres is appropriate)
5. Whether the app needs AI agents or MCP servers
6. Whether the app needs an agent chat page (use the `useAgentChat` composable)
7. Whether `app.vue` needs a sidebar, tabs, or other navigation (and what it should look like)

Present the plan to the user and ask for approval before proceeding.

---

## Step 6: Build

Implement the plan:

1. Create pages in `pages/` (standard Nuxt file-based routing)
2. Extract reusable components into `components/`
3. Put shared logic in `composables/`
4. If the app needs navigation, add it to `app.vue` or to individual pages
5. Use `Pref<T>` for any persisted settings (see `pref.mdc`)
6. Use Vuetify components and the project's dark theme
7. Update `DESIGN.md` with what you built

**Use the pre-built platform utilities:**

- `useElementalSchema()` — schema discovery with caching, flavor/PID lookup helpers
- `buildGatewayUrl()`, `getApiKey()`, `padNeid()` from `utils/elementalHelpers`
- `searchEntities()`, `getEntityName()` from `utils/elementalHelpers`
- `useElementalClient()` from `@yottagraph-app/elemental-api/client`

**Follow the project's coding conventions:**

- `<script setup lang="ts">` for all Vue components
- TypeScript required
- Composables return `readonly()` refs with explicit setters

---

## Step 7: Verify

After building, check dependencies are installed and run a build:

```bash
test -d node_modules || npm install
npm run build
```

Fix any build errors.

Then suggest the user run `npm run dev` to preview their app locally.

---

## Step 8: Next Steps

> Your app is taking shape! Here's what you can do next:
>
> - **Preview locally** with `npm run dev`
> - **Push to deploy** -- Vercel auto-deploys on push to main
> - **Deploy an AI agent** -- run `/deploy_agent` when you have an agent ready
> - **Deploy an MCP server** -- run `/deploy_mcp` for tool servers
