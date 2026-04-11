"""
Research Agent — gathers financial data from the Elemental API.

The agent decides what data to fetch based on a finalized QueryRewrite.
Each tool call appends raw API results to an in-memory accumulator and
returns a read-only summary. When the agent is done, it calls
finalize_research() which returns the full accumulated JSON — captured
from the SSE function_response stream by the frontend.

Local testing:
    export ELEMENTAL_API_URL=https://stable-query.lovelace.ai
    export ELEMENTAL_API_TOKEN=<your-token>
    cd agents
    pip install -r researcher/requirements.txt
    adk web
"""

import json
import uuid

from google.adk.agents import Agent

try:
    from broadchurch_auth import elemental_client
except ImportError:
    from .broadchurch_auth import elemental_client


# ---------------------------------------------------------------------------
# In-memory accumulator keyed by session
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}


def _get_session(session_id: str | None = None) -> tuple[str, dict]:
    """Get or create a research session."""
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    sid = session_id or str(uuid.uuid4())
    _sessions[sid] = {"entity_data": {}, "macro_data": {}}
    return sid, _sessions[sid]


def _ensure_entity(session: dict, entity_name: str, neid: str) -> dict:
    """Ensure an entity bucket exists in the session."""
    if entity_name not in session["entity_data"]:
        session["entity_data"][entity_name] = {"neid": neid}
    return session["entity_data"][entity_name]


# Module-level session ID set by the first tool call
_active_session_id: str | None = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def get_news(entity_name: str, neid: str) -> str:
    """Fetch recent news articles linked to an entity.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of articles found (count, date range, sentiment).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "both"},
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No linked entities found for '{entity_name}'."

        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={"eids": json.dumps(eids[:30]), "include_attributes": "true"},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        articles = _extract_news(values)
        bucket.setdefault("news", []).extend(articles)

        if not articles:
            return f"No news articles found for '{entity_name}'."

        sentiments = [a["sentiment"] for a in articles if a.get("sentiment") is not None]
        dates = [a["date"] for a in articles if a.get("date")]
        avg_sent = round(sum(sentiments) / len(sentiments), 2) if sentiments else None
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        parts = [f"Found {len(articles)} news article(s) for '{entity_name}'."]
        parts.append(f"Date range: {date_range}.")
        if avg_sent is not None:
            parts.append(f"Average sentiment: {avg_sent} ({len(sentiments)} scored).")
        return " ".join(parts)
    except Exception as e:
        return f"Error fetching news for '{entity_name}': {e}"


def get_stock_prices(entity_name: str, neid: str) -> str:
    """Fetch OHLCV stock price data for an entity.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of price data found (count, date range, price range).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={"eids": json.dumps([neid]), "include_attributes": "true"},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        prices = _extract_prices(values)

        if not prices:
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
                props2 = elemental_client.post(
                    "/elemental/entities/properties",
                    data={"eids": json.dumps(linked_eids[:10]), "include_attributes": "true"},
                )
                props2.raise_for_status()
                prices = _extract_prices(props2.json().get("values", []))

        bucket.setdefault("stock_prices", []).extend(prices)

        if not prices:
            return f"No stock price data found for '{entity_name}'."

        dates = [p["date"] for p in prices if p.get("date")]
        closes = [p["close"] for p in prices if p.get("close") is not None]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"
        price_range = f"${min(closes):.2f} – ${max(closes):.2f}" if closes else "unknown"

        return (
            f"Found {len(prices)} price data point(s) for '{entity_name}'. "
            f"Date range: {date_range}. Close price range: {price_range}."
        )
    except Exception as e:
        return f"Error fetching stock data for '{entity_name}': {e}"


def get_filings(entity_name: str, neid: str) -> str:
    """Fetch SEC filings (10-K, 10-Q, 8-K, etc.) for a company.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of filings found (count, form types, date range).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "incoming"},
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
            data={"eids": json.dumps(eids[:30]), "include_attributes": "true"},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        filings = _extract_filings(values)
        bucket.setdefault("filings", []).extend(filings)

        if not filings:
            return f"No SEC filing data found for '{entity_name}'."

        form_types = list({f.get("form_type", "?") for f in filings})
        dates = [f["date"] for f in filings if f.get("date")]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        return (
            f"Found {len(filings)} filing(s) for '{entity_name}'. "
            f"Form types: {', '.join(form_types)}. Date range: {date_range}."
        )
    except Exception as e:
        return f"Error fetching filings for '{entity_name}': {e}"


def get_events(entity_name: str, neid: str) -> str:
    """Fetch events (mergers, IPOs, lawsuits, etc.) involving an entity.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of events found (count, categories).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "incoming"},
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
            data={"eids": json.dumps(eids[:30]), "include_attributes": "true"},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        events = _extract_events(values)
        bucket.setdefault("events", []).extend(events)

        if not events:
            return f"No event data found for '{entity_name}'."

        categories = list({e.get("category", "?") for e in events})
        return (
            f"Found {len(events)} event(s) for '{entity_name}'. "
            f"Categories: {', '.join(categories)}."
        )
    except Exception as e:
        return f"Error fetching events for '{entity_name}': {e}"


def get_relationships(entity_name: str, neid: str) -> str:
    """Fetch entities related to a given entity.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of related entities found (count, names).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "both"},
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": "50"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No related entities found for '{entity_name}'."

        names_resp = elemental_client.post(
            "/entities/names",
            json={"neids": eids[:30]},
        )
        names_resp.raise_for_status()
        names_map = names_resp.json().get("results", {})

        relationships = []
        for eid in eids[:30]:
            name = names_map.get(eid, eid)
            relationships.append({"neid": eid, "name": name})

        bucket.setdefault("relationships", []).extend(relationships)

        related_names = [r["name"] for r in relationships[:10]]
        remaining = len(relationships) - 10 if len(relationships) > 10 else 0
        summary = ", ".join(related_names)
        if remaining:
            summary += f", and {remaining} more"

        return f"Found {len(relationships)} related entities for '{entity_name}': {summary}."
    except Exception as e:
        return f"Error fetching relationships for '{entity_name}': {e}"


def get_macro(query: str) -> str:
    """Search for macroeconomic data series (FRED).

    Args:
        query: Natural language description (e.g. "federal funds rate", "GDP growth").

    Returns:
        Summary of matching data series (count, names, latest values).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid

    try:
        find_expr = json.dumps({
            "type": "comparison",
            "comparison": {"operator": "string_like", "pid": 8, "value": query},
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
                by_entity[eid] = {"neid": eid}
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
                by_entity[eid].setdefault("data_points", []).append(
                    {"pid": pid, "value": val, "recorded_at": v.get("recorded_at", "")}
                )

        series_list = list(by_entity.values())
        session.setdefault("macro_data", {})[query] = series_list

        series_names = [s.get("name", s.get("neid", "?")) for s in series_list[:5]]
        return (
            f"Found {len(series_list)} macro data series matching '{query}': "
            f"{', '.join(series_names)}."
        )
    except Exception as e:
        return f"Error searching macro data for '{query}': {e}"


def get_entity_properties(entity_name: str, neid: str) -> str:
    """Fetch all properties for an entity.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of properties found (count, property names).
    """
    global _active_session_id
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        props_resp = elemental_client.post(
            "/elemental/entities/properties",
            data={"eids": json.dumps([neid]), "include_attributes": "true"},
        )
        props_resp.raise_for_status()
        values = props_resp.json().get("values", [])

        bucket.setdefault("properties", []).extend(values)

        unique_pids = list({v.get("pid") for v in values})
        prop_names = list({str(v.get("name", "")) for v in values if v.get("name")})[:10]

        return (
            f"Found {len(values)} property value(s) for '{entity_name}' "
            f"across {len(unique_pids)} unique properties. "
            f"Property names include: {', '.join(prop_names)}."
        )
    except Exception as e:
        return f"Error fetching properties for '{entity_name}': {e}"


def finalize_research() -> str:
    """Return the full accumulated research JSON.

    Call this when you have gathered enough data. The returned JSON contains
    all raw data collected during this session.

    Returns:
        The complete research JSON as a string.
    """
    global _active_session_id
    if not _active_session_id or _active_session_id not in _sessions:
        return json.dumps({"error": "No active research session"})

    session = _sessions[_active_session_id]
    result = json.dumps(session, default=str)

    del _sessions[_active_session_id]
    _active_session_id = None

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_news(values: list[dict]) -> list[dict]:
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


def _extract_prices(values: list[dict]) -> list[dict]:
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
        if ticker:
            p["ticker"] = ticker
    return result


def _extract_filings(values: list[dict]) -> list[dict]:
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

    events = []
    for _eid, info in by_entity.items():
        if info.get("category") or info.get("description"):
            events.append(info)
    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    return events


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

INSTRUCTION = """\
You are a financial data researcher. Your job is to gather data relevant to
a financial thesis by calling the available research tools.

## Input

You are given a finalized QueryRewrite JSON containing:
- `thesis_plaintext`: the user's thesis
- `entities`: list of resolved entities, each with `name`, `neid`, and `type`
- `claims`: testable claims to investigate
- `data_needs`: categories of data to fetch (news, stock_prices, filings, etc.)
- Optionally `macro_indicators`: macroeconomic concepts with search queries

## Your task

1. For EACH resolved entity, call the appropriate tools based on `data_needs`.
2. If `data_needs` includes "macro" or there are `macro_indicators`, call
   `get_macro` for each indicator's `search_query`.
3. Examine the summaries returned. If data seems thin, try related entities
   via `get_relationships`, then fetch data for those too.
4. When you have enough data, call `finalize_research()` to return the
   accumulated results.

## Rules

- Call tools for EVERY entity and data need. Do not skip any.
- If a tool call fails, try once more. If it still fails, move on.
- You may call `get_relationships` to discover additional relevant entities.
- Do NOT fabricate data. You can only read summaries.
- When done, call `finalize_research()` — this is mandatory.
- Do NOT write a report or analysis. Just gather data.
"""

root_agent = Agent(
    model="gemini-2.0-flash",
    name="researcher",
    instruction=INSTRUCTION,
    tools=[
        get_news,
        get_stock_prices,
        get_filings,
        get_events,
        get_relationships,
        get_macro,
        get_entity_properties,
        finalize_research,
    ],
)
