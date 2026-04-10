# Deploy Agent

Deploy an ADK agent from the `agents/` directory to Vertex AI Agent Engine via the Broadchurch Portal.

## Overview

This command deploys a Python ADK agent by triggering a GitHub Actions workflow through the Portal API. No local GCP credentials are needed -- the workflow authenticates via Workload Identity Federation.

The agent must live in `agents/<name>/` with the standard ADK structure (`agent.py`, `__init__.py`, `requirements.txt`).

**Prerequisite:** The project must have a valid `broadchurch.yaml` (created during provisioning).

---

## Step 1: Read Configuration

Read `broadchurch.yaml` from the project root.

```bash
cat broadchurch.yaml
```

**If the file does not exist:**

> This project hasn't been provisioned yet. Create it in the Broadchurch Portal first.

Stop here.

Extract these values:

- `tenant.org_id` (tenant org ID)
- `gateway.url` (Portal Gateway URL)

---

## Step 2: Discover Agents

List the directories under `agents/`:

```bash
ls -d agents/*/
```

**If no directories exist:**

> No agents found. Create one by making a directory under `agents/` with the following structure:
>
> ```
> agents/my_agent/
> ├── __init__.py
> ├── agent.py       # Your ADK agent definition (must export root_agent)
> └── requirements.txt
> ```
>
> See the `agents.mdc` rule for guidance on writing ADK agents.

Stop here.

**If multiple agents exist:** Ask the user which one to deploy.

**If only one agent exists:** Confirm with the user.

**Important:** Agent directory names must use underscores, not hyphens (e.g., `my_agent` not `my-agent`).

---

## Step 3: Validate Agent Structure

For the selected agent directory, verify the required files exist:

```bash
ls agents/<name>/__init__.py agents/<name>/agent.py agents/<name>/requirements.txt
```

**If any are missing:** Tell the user what's needed.

Also verify that `agent.py` exports a `root_agent`:

```bash
grep -l "root_agent" agents/<name>/agent.py
```

---

## Step 4: Ensure Code is Pushed

The deployment workflow runs on the code in the GitHub repo, not the local working directory. Make sure the agent code is committed and pushed:

```bash
git status
```

**If there are uncommitted changes in `agents/<name>/`:**

> Your agent code has local changes that aren't pushed yet. The deployment will use the version on GitHub. Would you like me to commit and push first?

If yes, commit and push. If no, warn them and continue.

---

## Step 5: Trigger Deployment

Call the Portal API to trigger the deploy workflow:

```bash
curl -sf -X POST "<GATEWAY_URL>/api/projects/<ORG_ID>/deploy" \
  -H "Content-Type: application/json" \
  -d '{"type": "agent", "name": "<AGENT_NAME>"}'
```

**If this fails with 404:** The agent directory may not exist on GitHub yet. Push your code first.

**If this succeeds:** The Portal has triggered the `deploy-agent.yml` GitHub Actions workflow.

---

## Step 6: Monitor Progress

> Deployment triggered! The agent is being deployed via GitHub Actions.
>
> - **Agent:** <name>
> - **Workflow:** deploy-agent.yml
>
> This typically takes 2-5 minutes. You can monitor progress:
>
> - In the Broadchurch Portal under your project's Deployment Status section
> - On GitHub: `https://github.com/<REPO>/actions`
>
> Once complete, the agent will automatically appear in your app's `/chat` page.

---

## Troubleshooting

### Deployment workflow fails

Check the GitHub Actions logs for the `Deploy Agent` workflow. Common issues:

- **"agent.py does not export root_agent"**: Ensure your agent module defines `root_agent`.
- **"Module not found"**: All dependencies must be in the agent's `requirements.txt`.
- **WIF auth failure**: The Broadchurch admin needs to run the WIF setup script (`portal/scripts/setup-wif.sh`).

### Agent directory naming

ADK requires directory names to use underscores (Python package convention). If your directory uses hyphens, rename it:

```bash
mv agents/my-agent agents/my_agent
```

### "No agents found" after successful deploy

The workflow registers the agent with the Portal automatically. If it doesn't appear, check the workflow logs for the "Register with Portal" step.
