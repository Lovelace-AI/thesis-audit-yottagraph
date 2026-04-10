"""
Example Elemental Agent — a minimal ADK agent for querying the yottagraph.

This is a starting point. Customize the instruction, add tools, and modify
the API calls to fit your use case.

Auth is handled automatically by broadchurch_auth (bundled at deploy time):
- Local dev: set ELEMENTAL_API_URL and ELEMENTAL_API_TOKEN env vars
- Production: reads broadchurch.yaml and mints GCP ID tokens

Local testing:
    export ELEMENTAL_API_URL=https://stable-query.lovelace.ai
    export ELEMENTAL_API_TOKEN=<your-token>
    cd agents
    pip install -r example_agent/requirements.txt
    adk web

Deployment:
    Use the /deploy_agent Cursor command or trigger the deploy-agent workflow.
"""

import json
import os

from google.adk.agents import Agent

try:
    from broadchurch_auth import elemental_client  # local dev (agents/ on sys.path)
except ImportError:
    from .broadchurch_auth import elemental_client  # Agent Engine (packaged inside ADK module)


def get_schema() -> dict:
    """Get the yottagraph schema: entity types (flavors) and properties.

    Call this to discover what kinds of entities exist (companies, people,
    organizations, etc.) and what properties they have (name, country,
    industry, etc.). Returns flavor IDs (fid) and property IDs (pid)
    needed for other queries.
    """
    resp = elemental_client.get("/elemental/metadata/schema")
    resp.raise_for_status()
    return resp.json()


def find_entities(expression: str, limit: int = 10) -> dict:
    """Search for entities in the yottagraph.

    Args:
        expression: JSON string with search criteria. Examples:
            - By type: {"type": "is_type", "is_type": {"fid": 10}}
            - Natural language: {"type": "natural_language", "natural_language": "companies in the technology sector"}
            - Combine: {"type": "and", "and": [<expr1>, <expr2>]}
        limit: Max results (default 10, max 10000).

    Returns:
        Dict with 'eids' (entity IDs) and 'op_id'.
    """
    resp = elemental_client.post("/elemental/find", data={"expression": expression, "limit": str(limit)})
    resp.raise_for_status()
    return resp.json()


def get_properties(eids: list[str], pids: list[int] | None = None) -> dict:
    """Get property values for entities.

    Args:
        eids: Entity IDs from find_entities.
        pids: Optional property IDs to retrieve (omit for all).

    Returns:
        Dict with 'values' containing property data per entity.
    """
    form_data: dict = {"eids": json.dumps(eids), "include_attributes": "true"}
    if pids is not None:
        form_data["pids"] = json.dumps(pids)
    resp = elemental_client.post("/elemental/entities/properties", data=form_data)
    resp.raise_for_status()
    return resp.json()


def lookup_entity(name: str) -> dict:
    """Look up an entity by name (e.g., "Apple", "Elon Musk").

    Args:
        name: Entity name to search for.

    Returns:
        Search results with ranked matches including NEIDs and names.
    """
    resp = elemental_client.post(
        "/entities/search",
        json={
            "queries": [{"queryId": 1, "query": name}],
            "maxResults": 5,
            "includeNames": True,
            "includeFlavors": True,
        },
    )
    resp.raise_for_status()
    return resp.json()


# --- MCP Server integration ---
# If MCP_SERVER_URL is set, the agent will also have access to tools from
# the connected MCP server (e.g., the example-server with hello, get_current_time, echo_data).
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")

_tools: list = [get_schema, find_entities, get_properties, lookup_entity]

if MCP_SERVER_URL:
    from google.adk.tools.mcp_tool import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

    _tools.append(McpToolset(connection_params=SseConnectionParams(url=MCP_SERVER_URL)))

# --- Customize below this line ---

root_agent = Agent(
    model="gemini-2.0-flash",
    name="example_agent",
    instruction="""You are an assistant that helps users explore the Elemental yottagraph,
a knowledge graph of real-world entities.

You can:
1. Look up the schema to understand entity types and properties
2. Search for entities by type, property values, or natural language
3. Retrieve detailed property values for specific entities
4. Look up entities by name

If you have access to MCP tools (hello, get_current_time, echo_data), you can
also demonstrate those when asked. These come from a connected MCP server.

Start by understanding what the user wants to know, then use the appropriate
tools to find the answer. Present results clearly and concisely.""",
    tools=_tools,
)
