"""
Thesis Researcher Agent — audits financial theses against the Lovelace yottagraph.

Two-phase workflow:
  Phase 1 (Clarification): Parse thesis, resolve entities, return candidates for
  user confirmation.
  Phase 2 (Research): After user confirms entities, gather evidence and organize
  into supporting vs contradicting signals.

Local testing:
    export ELEMENTAL_API_URL=https://stable-query.lovelace.ai
    export ELEMENTAL_API_TOKEN=<your-token>
    cd agents
    pip install -r thesis_researcher/requirements.txt
    adk web
"""

import json

from google.adk.agents import Agent

try:
    from broadchurch_auth import elemental_client
except ImportError:
    from .broadchurch_auth import elemental_client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def lookup_entity(name: str) -> str:
    """Look up an entity by name and return ranked candidates with scores.

    Use this to resolve entity names mentioned in a thesis (companies, people,
    organizations) to their yottagraph identifiers. Returns up to 5 candidates
    sorted by relevance score.

    Args:
        name: Entity name to search for (e.g. "Netflix", "Elon Musk", "JPMorgan").

    Returns:
        Formatted list of matching entities with name, NEID, type, and score.
    """
    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": name}],
                "maxResults": 5,
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results or not results[0].get("matches"):
            return f"No entities found matching '{name}'."

        matches = results[0]["matches"]
        lines = [f"Search results for '{name}' ({len(matches)} match(es)):"]
        for i, m in enumerate(matches):
            score_pct = round((m.get("score", 0)) * 100)
            lines.append(
                f"  {i + 1}. {m.get('name', '?')} "
                f"(NEID: {m.get('neid', '?')}, "
                f"type: {m.get('flavor', '?')}, "
                f"score: {score_pct}%)"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error looking up '{name}': {e}"


def get_entity_news(entity_name: str) -> str:
    """Get recent news articles mentioning an entity.

    Resolves the entity by name, then finds news articles linked to it via the
    'appears_in' relationship. Returns article titles, dates, and sentiment.

    Args:
        entity_name: Name of the entity to find news for.

    Returns:
        Formatted list of recent news articles with titles and sentiment.
    """
    try:
        neid = _resolve_top_entity(entity_name)
        if not neid:
            return f"Could not resolve entity '{entity_name}'."

        linked_expr = json.dumps({
            "type": "linked",
            "linked": {
                "to_entity": neid,
                "distance": 1,
                "direction": "both",
            },
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        find_data = resp.json()
        eids = find_data.get("eids", [])
        if not eids:
            return f"No linked entities found for '{entity_name}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={
                "eids": json.dumps(eids[:30]),
                "include_attributes": "true",
            },
        )
        props_resp.raise_for_status()
        props_data = props_resp.json()

        articles = _extract_news_from_properties(props_data.get("values", []))
        if not articles:
            return f"No news articles found linked to '{entity_name}'."

        lines = [f"News for '{entity_name}' (NEID: {neid}, {len(articles)} article(s)):"]
        for a in articles[:15]:
            url_part = f", URL: {a['url']}" if a.get("url") else ""
            lines.append(
                f"  - [{a.get('date', '?')}] {a.get('title', 'Untitled')} "
                f"(NEID: {a.get('neid', '?')}{url_part})"
            )
            if a.get("sentiment"):
                lines.append(f"    Sentiment: {a['sentiment']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching news for '{entity_name}': {e}"


def get_stock_prices(entity_name: str) -> str:
    """Get recent stock price data for a publicly traded entity.

    Resolves the entity by name, finds its linked financial instrument via the
    'traded_as' relationship, and retrieves OHLCV price data.

    Args:
        entity_name: Name of the company or ticker symbol.

    Returns:
        Formatted stock price data with recent OHLCV values.
    """
    try:
        neid = _resolve_top_entity(entity_name)
        if not neid:
            return f"Could not resolve entity '{entity_name}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={
                "eids": json.dumps([neid]),
                "include_attributes": "true",
            },
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        price_data = _extract_price_data(values, neid)
        instrument_neid = neid
        if not price_data:
            linked_expr = json.dumps({
                "type": "linked",
                "linked": {"to_entity": neid, "distance": 1, "direction": "both"},
            })
            find_resp = elemental_client.post(
                "/elemental/find",
                data={"expression": linked_expr, "limit": "20"},
            )
            find_resp.raise_for_status()
            linked_eids = find_resp.json().get("eids", [])
            if linked_eids:
                instrument_neid = linked_eids[0]
                props2 = elemental_client.post(
                    "/elemental/entities/properties",
                    data={
                        "eids": json.dumps(linked_eids[:10]),
                        "include_attributes": "true",
                    },
                )
                props2.raise_for_status()
                price_data = _extract_price_data(props2.json().get("values", []), instrument_neid)

        if not price_data:
            return f"No stock price data found for '{entity_name}'."

        ticker = price_data[0].get("ticker", "")
        ticker_part = f", ticker: {ticker}" if ticker else ""
        lines = [
            f"Stock data for '{entity_name}' "
            f"(NEID: {instrument_neid}{ticker_part}, "
            f"{len(price_data)} data point(s)):"
        ]
        for p in price_data[:20]:
            lines.append(
                f"  [{p.get('date', '?')}] "
                f"O:{p.get('open', '?')} H:{p.get('high', '?')} "
                f"L:{p.get('low', '?')} C:{p.get('close', '?')} "
                f"V:{p.get('volume', '?')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching stock data for '{entity_name}': {e}"


def get_entity_filings(entity_name: str) -> str:
    """Get SEC filings (10-K, 10-Q, 8-K, etc.) for a company.

    Resolves the entity by name and retrieves linked SEC filing documents.

    Args:
        entity_name: Name of the company to find filings for.

    Returns:
        Formatted list of SEC filings with type, date, and description.
    """
    try:
        neid = _resolve_top_entity(entity_name)
        if not neid:
            return f"Could not resolve entity '{entity_name}'."

        linked_expr = json.dumps({
            "type": "linked",
            "linked": {
                "to_entity": neid,
                "distance": 1,
                "direction": "incoming",
            },
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No filings found for '{entity_name}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={
                "eids": json.dumps(eids[:30]),
                "include_attributes": "true",
            },
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        filings = _extract_filings(values)
        if not filings:
            return f"No SEC filing data found for '{entity_name}'."

        lines = [f"Filings for '{entity_name}' (NEID: {neid}, {len(filings)} filing(s)):"]
        for f in filings[:15]:
            acc = f.get("accession_number")
            acc_part = f", accession: {acc}" if acc else ""
            lines.append(
                f"  - [{f.get('date', '?')}] {f.get('form_type', '?')}: "
                f"{f.get('description', 'No description')} "
                f"(NEID: {f.get('neid', '?')}{acc_part})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching filings for '{entity_name}': {e}"


def get_entity_relationships(entity_name: str, direction: str = "both") -> str:
    """Get entities related to a given entity (subsidiaries, officers, owners, etc.).

    Finds entities linked to the target through the knowledge graph.

    Args:
        entity_name: Name of the entity to explore relationships for.
        direction: 'outgoing' (what does it own/link to), 'incoming' (who links to it),
                   or 'both' (default).

    Returns:
        Formatted list of related entities with their types and relationship context.
    """
    try:
        neid = _resolve_top_entity(entity_name)
        if not neid:
            return f"Could not resolve entity '{entity_name}'."

        linked_expr = json.dumps({
            "type": "linked",
            "linked": {
                "to_entity": neid,
                "distance": 1,
                "direction": direction,
            },
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No related entities found for '{entity_name}' (direction={direction})."

        names_resp = elemental_client.post(
            "/entities/names",
            json={"neids": eids[:30]},
        )
        names_resp.raise_for_status()
        names_map = names_resp.json().get("results", {})

        lines = [f"Entities related to '{entity_name}' ({len(eids)} found, showing up to 30):"]
        for eid in eids[:30]:
            name = names_map.get(eid, eid)
            lines.append(f"  - {name} (NEID: {eid})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching relationships for '{entity_name}': {e}"


def get_entity_events(entity_name: str) -> str:
    """Get significant events involving an entity (mergers, IPOs, lawsuits, etc.).

    Finds events where the entity is listed as a participant.

    Args:
        entity_name: Name of the entity to find events for.

    Returns:
        Formatted list of events with category, date, description, and entity role.
    """
    try:
        neid = _resolve_top_entity(entity_name)
        if not neid:
            return f"Could not resolve entity '{entity_name}'."

        linked_expr = json.dumps({
            "type": "linked",
            "linked": {
                "to_entity": neid,
                "distance": 1,
                "direction": "incoming",
            },
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No events found for '{entity_name}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={
                "eids": json.dumps(eids[:30]),
                "include_attributes": "true",
            },
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        events = _extract_events(values)
        if not events:
            return f"No event data found for '{entity_name}'."

        lines = [f"Events involving '{entity_name}' (NEID: {neid}, {len(events)} event(s)):"]
        for ev in events[:15]:
            lines.append(
                f"  - [{ev.get('date', '?')}] {ev.get('category', '?')}: "
                f"{ev.get('description', 'No description')} "
                f"(NEID: {ev.get('neid', '?')})"
            )
            if ev.get("likelihood"):
                lines.append(f"    Likelihood: {ev['likelihood']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching events for '{entity_name}': {e}"


def get_macro_data(query: str) -> str:
    """Search for macroeconomic data series by description.

    Searches for FRED economic data series related to the query. Useful for
    finding GDP, employment, inflation, interest rate, and other macro data
    relevant to a financial thesis.

    Args:
        query: Natural language description of the data series to find
               (e.g. "US unemployment rate", "GDP growth", "federal funds rate").

    Returns:
        Formatted list of matching economic data series with descriptions and values.
    """
    try:
        find_expr = json.dumps({
            "type": "comparison",
            "comparison": {
                "operator": "string_like",
                "pid": 8,
                "value": query,
            },
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": find_expr, "limit": "20"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No macro data series found matching '{query}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={"eids": json.dumps(eids[:10])},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        by_entity: dict[str, dict] = {}
        for v in values:
            eid = v.get("eid", "")
            if eid not in by_entity:
                by_entity[eid] = {}
            pid = v.get("pid")
            val = v.get("value")
            name_str = str(v.get("name", "")).lower()
            if pid == 8:
                by_entity[eid]["name"] = val
            elif "fred_series_id" in name_str and val:
                by_entity[eid]["fred_series_id"] = str(val)
            elif "release_link" in name_str and val:
                by_entity[eid]["release_link"] = str(val)
            else:
                by_entity[eid].setdefault("properties", []).append(
                    {"pid": pid, "value": val, "recorded_at": v.get("recorded_at", "")}
                )

        lines = [f"Macro data matching '{query}' ({len(by_entity)} series):"]
        for eid, info in list(by_entity.items())[:10]:
            name = info.get("name", eid)
            series_id = info.get("fred_series_id", "")
            url = info.get("release_link") or (
                f"https://fred.stlouisfed.org/series/{series_id}" if series_id else ""
            )
            url_part = f", URL: {url}" if url else ""
            lines.append(f"  - {name} (NEID: {eid}{url_part})")
            for prop in (info.get("properties") or [])[:3]:
                lines.append(f"    {prop['value']} (recorded: {prop['recorded_at']})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching macro data for '{query}': {e}"


def get_schema() -> str:
    """Get the yottagraph schema: available entity types and their properties.

    Call this to discover what kinds of entities exist (companies, people,
    organizations, filings, etc.) and what properties and relationships are
    available. Use this when you need to explore the data model beyond the
    pre-built tools.

    Returns:
        Formatted summary of entity types (flavors) and their properties.
    """
    try:
        resp = elemental_client.get("/elemental/metadata/schema")
        resp.raise_for_status()
        schema = resp.json()

        flavors = schema.get("flavors", [])
        properties = schema.get("properties", [])

        lines = [f"Yottagraph schema: {len(flavors)} entity types, {len(properties)} properties"]
        lines.append("\nEntity types (flavors):")
        for f in flavors[:30]:
            fid = f.get("fid", f.get("findex", "?"))
            name = f.get("name", "?")
            lines.append(f"  - {name} (FID: {fid})")

        lines.append(f"\nProperties ({len(properties)} total, showing first 40):")
        for p in properties[:40]:
            pid = p.get("pid", p.get("pindex", "?"))
            name = p.get("name", "?")
            ptype = p.get("type", "?")
            lines.append(f"  - {name} (PID: {pid}, type: {ptype})")

        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching schema: {e}"


# ---------------------------------------------------------------------------
# Internal helpers — not exposed as tools
# ---------------------------------------------------------------------------

def _resolve_top_entity(name: str) -> str | None:
    """Resolve an entity name to the top-scoring NEID."""
    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": name}],
                "maxResults": 1,
                "includeNames": True,
                "includeFlavors": True,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results and results[0].get("matches"):
            return results[0]["matches"][0]["neid"]
    except Exception:
        pass
    return None


def _extract_news_from_properties(values: list[dict]) -> list[dict]:
    """Extract news article info from property values."""
    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        pid = v.get("pid")
        val = v.get("value")
        if pid == 8:
            by_entity[eid]["title"] = val
        by_entity[eid]["date"] = by_entity[eid].get("date") or v.get("recorded_at", "")
        attrs = v.get("attributes", {})
        if attrs:
            for _aid, attr_val in attrs.items():
                if isinstance(attr_val, str) and attr_val.startswith("http"):
                    by_entity[eid]["url"] = attr_val
                elif isinstance(attr_val, (int, float)) and -1 <= attr_val <= 1:
                    by_entity[eid]["sentiment"] = attr_val

    articles = []
    for eid, info in by_entity.items():
        if info.get("title"):
            articles.append({
                "neid": info.get("neid", eid),
                "title": info["title"],
                "date": (info.get("date") or "")[:10],
                "sentiment": info.get("sentiment"),
                "url": info.get("url"),
            })
    articles.sort(key=lambda a: a.get("date", ""), reverse=True)
    return articles


def _extract_price_data(values: list[dict], neid: str | None = None) -> list[dict]:
    """Extract OHLCV stock price data from property values."""
    price_points: dict[str, dict] = {}
    ticker = None
    for v in values:
        name_str = str(v.get("name", "")).lower()
        val = v.get("value")
        if "ticker" in name_str and val:
            ticker = str(val)
        recorded = v.get("recorded_at", "")
        date_key = recorded[:10] if recorded else ""
        if not date_key:
            continue
        if date_key not in price_points:
            price_points[date_key] = {"date": date_key}
        for key in ("open", "high", "low", "close", "volume"):
            if key in name_str:
                price_points[date_key][key] = val
                break

    result = sorted(price_points.values(), key=lambda p: p.get("date", ""), reverse=True)
    for p in result:
        if neid:
            p["neid"] = neid
        if ticker:
            p["ticker"] = ticker
    return result


def _extract_filings(values: list[dict]) -> list[dict]:
    """Extract SEC filing info from property values."""
    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        pid = v.get("pid")
        val = v.get("value")
        name_str = str(v.get("name", "")).lower()
        if pid == 8:
            by_entity[eid]["description"] = val
        if "accession" in name_str and val:
            by_entity[eid]["accession_number"] = str(val)
        by_entity[eid].setdefault("date", v.get("recorded_at", "")[:10])
        val_str = str(val).lower() if val else ""
        for form in ["10-k", "10-q", "8-k", "def 14a", "13f", "sc 13"]:
            if form in val_str:
                by_entity[eid]["form_type"] = val
                break

    filings = []
    for eid, info in by_entity.items():
        if info.get("form_type") or info.get("description"):
            filings.append({
                "neid": info.get("neid", eid),
                "form_type": info.get("form_type", "Unknown"),
                "date": info.get("date", "?"),
                "description": info.get("description", ""),
                "accession_number": info.get("accession_number"),
            })
    filings.sort(key=lambda f: f.get("date", ""), reverse=True)
    return filings


def _extract_events(values: list[dict]) -> list[dict]:
    """Extract event info from property values."""
    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        val = v.get("value")
        name = str(v.get("name", "")).lower() if v.get("name") else ""
        pid = v.get("pid")
        if pid == 8:
            by_entity[eid]["description"] = val
        elif "category" in name or "event_category" in name:
            by_entity[eid]["category"] = val
        elif "date" in name or "event_date" in name:
            by_entity[eid]["date"] = val
        elif "likelihood" in name:
            by_entity[eid]["likelihood"] = val
        elif "description" in name or "event_description" in name:
            by_entity[eid]["description"] = val

    events = []
    for eid, info in by_entity.items():
        if info.get("category") or info.get("description"):
            events.append(info)
    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    return events


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

INSTRUCTION = """\
You are a financial research analyst. Your job is to audit investment theses
by gathering evidence from the Lovelace yottagraph (a knowledge graph of
financial entities, news, filings, events, and market data).

You operate in two phases:

## PHASE 1: CLARIFICATION

When a user submits a thesis for the first time, you MUST:

1. Restate the thesis in clear, testable terms.
2. Identify every entity mentioned or implied (companies, people, sectors, etc.).
3. Call lookup_entity for EACH entity to find candidates in the yottagraph.
4. Identify the specific testable claims in the thesis.
5. Return your response as a JSON block wrapped in ```json fences with this
   EXACT structure:

```json
{
  "type": "clarification",
  "thesis_parsed": "Your restated version of the thesis",
  "entities": [
    {
      "mentioned_as": "what the user wrote",
      "candidates": [
        {
          "resolved_name": "Official Name",
          "neid": "20-digit-id",
          "entity_type": "organization",
          "description": "Brief description of this entity",
          "score": 0.95
        }
      ],
      "selected_index": 0
    }
  ],
  "claims": ["Testable claim 1", "Testable claim 2"]
}
```

Rules for Phase 1:
- For entities with clear top matches (score > 0.8), set selected_index to 0.
- For ambiguous entities, include all candidates and set selected_index to null.
- For entities that can't be resolved (e.g. "its competitors"), set candidates
  to [] and add a "needs_clarification" field with your suggestion.
- ALWAYS include the JSON block. No other format is acceptable.
- Do NOT proceed to research until the user confirms.

## PHASE 2: RESEARCH

When the user confirms their entity selections (or provides corrections),
proceed with full research:

1. Plan your research: decide which tools to call based on the thesis claims.
2. Execute: gather data systematically. Check news, stock data, filings,
   relationships, and events for each relevant entity.
3. Iterate: if initial findings suggest new leads, follow them.
4. Classify each piece of evidence as "supporting" or "contradicting".
5. Synthesize: write analysis summaries for each side.
6. Return your response as a JSON block wrapped in ```json fences:

```json
{
  "type": "results",
  "thesis_parsed": "Restated thesis",
  "entities_examined": ["Entity 1", "Entity 2"],
  "supporting": {
    "evidence": [
      {
        "source": "news",
        "title": "Short description of the evidence",
        "detail": "What was found and why it supports the thesis",
        "date": "2025-01-15",
        "entity": "Entity Name",
        "neid": "the NEID from the tool output that this fact came from",
        "source_url": "URL if available (article URL, FRED link, etc.)",
        "tool_used": "get_entity_news"
      }
    ],
    "analysis": "Your synthesis of the overall supporting case"
  },
  "contradicting": {
    "evidence": [
      {
        "source": "stock",
        "title": "Short description",
        "detail": "What was found and why it contradicts the thesis",
        "date": "2025-03-10",
        "entity": "Entity Name",
        "neid": "the NEID from the tool output",
        "source_url": "URL if available",
        "tool_used": "get_stock_prices"
      }
    ],
    "analysis": "Your synthesis of the overall contradicting case"
  },
  "limitations": "What you couldn't verify, data gaps, or caveats"
}
```

Rules for Phase 2:
- The "source" field must be one of: news, filing, stock, event, relationship, macro.
- EVERY evidence item MUST include "neid" and "tool_used" from the tool output.
  NEIDs are 20-digit numeric strings like "00327906234544446929". Copy them
  EXACTLY as they appear in the tool's response. NEVER invent or abbreviate NEIDs.
  If the tool output says "NEID: 00327906234544446929", use "00327906234544446929".
- Include "source_url" when the tool provided a URL (article links, FRED URLs, etc.).
- Be thorough: check multiple data types for each entity.
- Be balanced: actively look for BOTH supporting and contradicting evidence.
- If you find no evidence for one side, say so honestly in the analysis.
- Include the limitations field to note data gaps or caveats.
- ALWAYS include the JSON block. No other format is acceptable.

## HANDLING CORRECTIONS

If the user provides entity corrections (e.g. "I meant JP Morgan Chase the bank,
not Morgan Stanley"), re-resolve those entities using lookup_entity and return
an updated Phase 1 clarification response.

If the user's confirmation message includes specific NEIDs for selected entities,
use those directly — do not re-resolve them.
"""

root_agent = Agent(
    model="gemini-2.0-flash",
    name="thesis_researcher",
    instruction=INSTRUCTION,
    tools=[
        lookup_entity,
        get_entity_news,
        get_stock_prices,
        get_entity_filings,
        get_entity_relationships,
        get_entity_events,
        get_macro_data,
        get_schema,
    ],
)
