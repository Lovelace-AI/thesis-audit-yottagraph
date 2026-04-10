# AGENTS.md

Broadchurch tenant application built on Aether (Nuxt 3 + Vuetify).

## Cursor Cloud specific instructions

If you are in Cursor Cloud, the `environment.json` install step runs
`init-project.js` and `npm install` automatically, and starts a dev server
on port 3000.

**Verify before starting:** check that `.env` exists and `node_modules/` is
present. If either is missing, run: `node init-project.js && npm install`

**Do NOT** manually run `nvm install` or `nvm use` — Node version is managed
by the environment.

### Initial environment setup only

This subsection applies ONLY when first asked to "set up the development
environment" or "demonstrate that the environment is working." It does
**NOT** apply to ongoing development — once the app is built, use the
browser normally to test and verify UI changes.

**During initial setup**, skip browser/UI testing. The starter UI is a
placeholder template that will be replaced by `/build_my_app`. Do not
launch a browser, record videos, or take screenshots at this stage.
Verifying `npm run build` passes is sufficient.

1. Check the "Dev Server" terminal output for a line containing
   `Listening on` or `Local: http://localhost:3000`. If present, the
   environment is working.
2. If the dev server is NOT running, start it with `npm run dev` and wait
   for the "Listening on" line.
3. Run `npm run build` to verify the project compiles.
4. Once confirmed, tell the user the environment is ready, then
   immediately run the `/build_my_app` command.

### MCP tools

Lovelace MCP servers (`lovelace-elemental`, `lovelace-stocks`, etc.)
should be available if configured at the org level. Check your tool list
for `elemental_*` tools. If they're not available, use the Elemental API
client (`useElementalClient()`) and the skill docs in
`.cursor/skills/elemental-api/` and `.cursor/skills/data-model/` for platform data access instead.

### Technical details

Node 20 is the baseline (`.nvmrc`). The `environment.json` install step
handles this via `nvm install 20 && nvm alias default 20`. Newer Node
versions (22, 25) generally work but may produce `EBADENGINE` warnings
during install — safe to ignore.

The install step runs `node init-project.js --local` (creates `.env` if
absent) then `npm install` (triggers `postinstall` → `nuxt prepare`).
Auth0 is bypassed via `NUXT_PUBLIC_USER_NAME=dev-user`
in the generated `.env`.

**No automated test suite.** Verification is `npm run build` (compile
check) and `npm run format:check` (Prettier). See Verification Commands.

**Before committing:** always run `npm run format` — the husky pre-commit
hook runs `lint-staged` with `prettier --check` and will reject
unformatted files.

## Manual / Local Setup

Node 20 is the baseline (pinned in `.nvmrc`). Newer versions generally work.

```bash
npm run init -- --local   # creates .env with dev defaults (no Auth0)
npm install               # all deps are public on npmjs.com -- no tokens needed
npm run dev               # dev server on port 3000
```

For the full interactive wizard (project name, Auth0, query server, etc.):

```bash
npm run init              # interactive, or --non-interactive for CI (see --help)
```

## .env Essentials

| Variable                           | Purpose                          | Default                                 |
| ---------------------------------- | -------------------------------- | --------------------------------------- |
| `NUXT_PUBLIC_APP_ID`               | Unique app identifier            | derived from directory name             |
| `NUXT_PUBLIC_APP_NAME`             | Display name                     | derived from directory name             |
| `NUXT_PUBLIC_USER_NAME`            | Set to any value to bypass Auth0 | `dev-user` in local mode                |
| `NUXT_PUBLIC_QUERY_SERVER_ADDRESS` | Query Server URL                 | read from `broadchurch.yaml` if present |
| `NUXT_PUBLIC_GATEWAY_URL`          | Portal Gateway for agent chat    | read from `broadchurch.yaml` if present |
| `NUXT_PUBLIC_TENANT_ORG_ID`        | Auth0 org ID for this tenant     | read from `broadchurch.yaml` if present |

See `.env.example` for the full list.

## Project Structure

| Directory      | Contents                                             | Deployed to            |
| -------------- | ---------------------------------------------------- | ---------------------- |
| `pages/`       | Nuxt pages (file-based routing)                      | Vercel (with app)      |
| `components/`  | Vue components                                       | Vercel (with app)      |
| `composables/` | Vue composables (auto-imported by Nuxt)              | Vercel (with app)      |
| `utils/`       | Utility functions (NOT auto-imported)                | Vercel (with app)      |
| `server/`      | Nitro API routes (KV storage, avatar proxy)          | Vercel (with app)      |
| `agents/`      | Python ADK agents (each subdirectory is deployable)  | Vertex AI Agent Engine |
| `mcp-servers/` | Python MCP servers (each subdirectory is deployable) | Cloud Run              |

### Cursor instructions (`.cursor/`)

Cursor rules, commands, and skills are installed from the
`@yottagraph-app/aether-instructions` npm package during project init.
`.cursor/skills/elemental-api/` contains API skill documentation (endpoint
reference, types, usage patterns). `.cursor/skills/data-model/` contains
Lovelace data model documentation (entity types, schemas per fetch source).
If these directories are missing, run `/update_instructions` to reinstall.

### Agents

`agents/example_agent/` is a working starter agent that queries the Elemental
Knowledge Graph. It includes schema discovery, entity search, property lookup,
and optional MCP server integration. Use it as a starting point — customize the
instruction, add tools, and see the `agents` cursor rule for the full guide.

## Configuration

`broadchurch.yaml` contains tenant-specific settings (GCP project, org ID,
service account, gateway URL, query server URL). It's generated during
provisioning and committed by the `tenant-init` workflow. Don't edit manually
unless you know what you're doing.

## Storage

Two storage services are available. Check `.env` to see which are connected:

| Store                  | How to check                | Env var                                 | Utility file                                                | Always available?                   |
| ---------------------- | --------------------------- | --------------------------------------- | ----------------------------------------------------------- | ----------------------------------- |
| **KV** (Upstash Redis) | `KV_REST_API_URL` in `.env` | `KV_REST_API_URL`, `KV_REST_API_TOKEN`  | `server/utils/redis.ts` (pre-scaffolded)                    | Yes                                 |
| **Neon Postgres**      | `DATABASE_URL` in `.env`    | `DATABASE_URL`, `DATABASE_URL_UNPOOLED` | `server/utils/neon.ts` (scaffolded if Neon was provisioned) | Only if enabled at project creation |

### Quick start

**KV** is ready to use out of the box. Use `getRedis()` from
`server/utils/redis.ts` in server routes, or `usePrefsStore()` on the client
(see `pref` rule for the `Pref<T>` pattern).

**Neon Postgres** — if `DATABASE_URL` is in `.env` and `server/utils/neon.ts`
exists, Postgres is ready. Use `getDb()` in server routes:

```typescript
import { getDb } from '~/server/utils/neon';

export default defineEventHandler(async () => {
    const sql = getDb();
    if (!sql) throw createError({ statusCode: 503, statusMessage: 'Database not configured' });
    return await sql`SELECT * FROM my_table`;
});
```

If `DATABASE_URL` is missing but you expected Postgres, the project may not
have been provisioned with it. Check the Broadchurch Portal dashboard to add
a database, then re-run `node init-project.js` to fetch credentials.

### Where credentials come from

**Deployed builds** (push to `main` → Vercel): storage env vars are
auto-injected and decrypted at runtime. Storage works with zero
configuration. **This is the primary development path** — push your code
and test on the deployed preview/production URL.

**Local dev / Cursor Cloud:** storage credentials are not yet available for
local use. `getRedis()` and `getDb()` will return `null`, and the app should
handle this gracefully (show a "not configured" state, use defaults, etc.).
KV preferences fall back to their default values. Postgres features should
check `getDb()` and show appropriate UI when it returns `null`.

This is a known platform limitation — the Broadchurch team is working on
making storage credentials available for local development.

See the `server` rule for detailed usage patterns for both KV and Postgres.

## How Deployment Works

### App (Nuxt UI + server routes)

Vercel auto-deploys on every push to `main`. Preview deployments are created for
other branches. The app is available at `{slug}.yottagraph.app`.

### Agents (`agents/`)

Each subdirectory in `agents/` is a self-contained Python ADK agent. Deploy via
the Portal UI or `/deploy_agent` in Cursor.

### MCP Servers (`mcp-servers/`)

Each subdirectory in `mcp-servers/` is a Python FastMCP server. Deploy via
the Portal UI or `/deploy_mcp` in Cursor.

## Verification Commands

```bash
npm run dev          # dev server -- check browser at localhost:3000
npm run build        # production build -- catches compile errors
npm run format       # Prettier formatting (run before committing)
```

## Known Issues

### Blank white page after `npm run dev`

If the server returns HTTP 200 but the page is blank, check the browser console
for `SyntaxError` about missing exports. This is caused by Nuxt's auto-import
scanner. **Fix:** verify the `imports:dirs` hook in `nuxt.config.ts` is present.

### Port 3000 conflict

The dev server binds to port 3000 by default. If another service is already
using that port, start with `PORT=3001 npm run dev`.

### Formatting

Pre-commit hook runs `lint-staged` with Prettier. Run `npm run format` before
committing to avoid failures.
