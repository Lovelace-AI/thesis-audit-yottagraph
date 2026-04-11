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
# Schema cache — PID ↔ name mapping loaded once from the Elemental API
# ---------------------------------------------------------------------------

_pid_to_name: dict[int, str] = {}
_name_to_pid: dict[str, int] = {}
_schema_loaded = False

_PRICE_FIELDS = ("open_price", "high_price", "low_price", "close_price", "trading_volume")
_FILING_FIELDS = (
    "accession_number", "form_type", "filing_date", "total_revenue", "net_income",
    "total_assets", "total_liabilities", "shareholders_equity", "shares_outstanding",
    "eps_basic", "eps_diluted",
)
_EVENT_FIELDS = (
    "category", "form_8k_event", "form_8k_item_code", "event_status",
    "likelihood", "description", "date",
)
_NEWS_FIELDS = ("title", "sentiment", "original_publication_name", "tone", "title_factuality")

_TICKER_PIDS: set[int] = set()
_PRICE_PIDS: set[int] = set()


def _load_schema() -> None:
    """Fetch the KG schema and build PID↔name maps. Idempotent."""
    global _schema_loaded, _TICKER_PIDS, _PRICE_PIDS
    if _schema_loaded:
        return
    try:
        resp = elemental_client.get("/elemental/metadata/schema")
        resp.raise_for_status()
        schema = resp.json().get("schema", resp.json())
        for p in schema.get("properties", []):
            pid = p.get("pid")
            name = p.get("name", "")
            if pid is not None and name:
                _pid_to_name[pid] = name
                _name_to_pid[name] = pid
        _TICKER_PIDS = {
            _name_to_pid.get("ticker_symbol", 0),
            _name_to_pid.get("ticker", 0),
        } - {0}
        _PRICE_PIDS = {_name_to_pid[n] for n in _PRICE_FIELDS if n in _name_to_pid}
    except Exception:
        pass
    _schema_loaded = True


def _pname(pid: int) -> str:
    """Resolve a PID to its property name, loading schema if needed."""
    _load_schema()
    return _pid_to_name.get(pid, "")


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
    _load_schema()
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
            data={"expression": linked_expr, "limit": "30"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No linked entities found for '{entity_name}'."

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
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

    Tries several strategies to locate price data:
    1. Direct property fetch on the entity (works for most stocks)
    2. If timeout, look up ticker via PID 169 on the organization entity
       and search for the financial_instrument entity by ticker
    3. Fall back to financial fundamentals from the org entity

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of price data found (count, date range, price range).
    """
    global _active_session_id
    _load_schema()
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    prices: list[dict] = []
    ticker = None

    # Strategy 1: direct fetch on the provided entity
    try:
        values = _fetch_properties([neid], timeout=15.0)
        prices = _extract_prices(values)
        ticker = _extract_ticker(values)
    except Exception:
        pass

    # Strategy 2: find the financial_instrument by ticker or name
    if not prices:
        if not ticker:
            ticker = _get_ticker_for_entity(neid)
        fi_neid = None
        if ticker:
            fi_neid = _find_financial_instrument(ticker)
        if not fi_neid:
            fi_neid = _find_financial_instrument(entity_name)
        if fi_neid and fi_neid != neid:
            try:
                values2 = _fetch_properties([fi_neid], timeout=15.0)
                prices = _extract_prices(values2)
                if not ticker:
                    ticker = _extract_ticker(values2)
            except Exception:
                pass

    # Strategy 3: financial fundamentals from the EDGAR organization entity
    if not prices:
        org_neid = _find_organization_with_data(entity_name)
        if org_neid:
            try:
                values3 = _fetch_properties([org_neid], timeout=15.0)
                fundamentals = _extract_fundamentals(values3)
                if fundamentals:
                    bucket["financial_fundamentals"] = fundamentals
                    if not ticker:
                        ticker = _extract_ticker(values3)
            except Exception:
                pass

    if ticker:
        bucket["ticker"] = ticker

    bucket.setdefault("stock_prices", []).extend(prices)

    if not prices and not bucket.get("financial_fundamentals"):
        return f"No stock price data found for '{entity_name}'."

    parts = []
    if prices:
        dates = [p["date"] for p in prices if p.get("date")]
        closes = [p["close"] for p in prices if p.get("close") is not None]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"
        price_range = f"${min(closes):.2f} – ${max(closes):.2f}" if closes else "unknown"
        parts.append(
            f"Found {len(prices)} OHLCV data point(s) for '{entity_name}'"
            + (f" (ticker: {ticker})" if ticker else "")
            + f". Date range: {date_range}. Close price range: {price_range}."
        )
    if bucket.get("financial_fundamentals"):
        f = bucket["financial_fundamentals"]
        parts.append(
            f"Financial fundamentals: revenue=${f.get('total_revenue', '?')}, "
            f"net_income=${f.get('net_income', '?')}, "
            f"shares_outstanding={f.get('shares_outstanding', '?')}."
        )
    return " ".join(parts)


def get_filings(entity_name: str, neid: str) -> str:
    """Fetch SEC filings (10-K, 10-Q, 8-K, etc.) for a company.

    Args:
        entity_name: Display name of the entity.
        neid: The 20-digit entity ID.

    Returns:
        Summary of filings found (count, form types, date range).
    """
    global _active_session_id
    _load_schema()
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
            data={"expression": linked_expr, "limit": "30"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No filings found for '{entity_name}'."

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
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
    _load_schema()
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
            data={"expression": linked_expr, "limit": "30"},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No events found for '{entity_name}'."

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
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
            timeout=10.0,
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
    """Search for macroeconomic concepts and their related entities.

    Uses the ``economic_concept`` entity flavor to find concepts like
    "federal funds rate", "GDP", "CPI", etc., then resolves their
    relationships (``financially_impacts``, ``appears_in``) to discover
    which financial instruments and organizations are affected.

    Args:
        query: Natural language description (e.g. "federal funds rate", "GDP growth").

    Returns:
        Summary of matching concepts and impacted entities.
    """
    global _active_session_id
    _load_schema()
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid

    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": query, "flavors": ["economic_concept"]}],
                "maxResults": 5,
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.3,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        matches = resp.json().get("results", [{}])[0].get("matches", [])
        if not matches:
            return f"No macro/economic concepts found matching '{query}'."

        concepts: list[dict] = []
        for m in matches[:3]:
            concept: dict = {
                "neid": m.get("neid", ""),
                "name": m.get("name", ""),
                "score": m.get("score"),
            }

            concept_neid = concept["neid"]
            try:
                values = _fetch_properties([concept_neid], timeout=10.0)
                fi_pid = _name_to_pid.get("financially_impacts")
                appears_pid = _name_to_pid.get("appears_in")
                impacted_neids: list[str] = []
                for v in values:
                    pid = v.get("pid")
                    val = v.get("value")
                    if pid == fi_pid and val:
                        impacted_neids.append(str(val))
                    pname = _pname(pid) if pid else ""
                    if pname and pname not in ("appears_in", "financially_impacts"):
                        concept.setdefault("properties", {})[pname] = val

                if impacted_neids:
                    names_resp = elemental_client.post(
                        "/entities/names",
                        json={"neids": impacted_neids[:10]},
                        timeout=10.0,
                    )
                    names_resp.raise_for_status()
                    names_map = names_resp.json().get("results", {})
                    concept["impacts"] = [
                        {"neid": neid, "name": names_map.get(neid, neid)}
                        for neid in impacted_neids[:10]
                    ]
            except Exception:
                pass

            concepts.append(concept)

        session.setdefault("macro_data", {})[query] = concepts

        summaries = []
        for c in concepts:
            detail = c["name"]
            impacts = c.get("impacts", [])
            if impacts:
                impact_names = [i["name"] for i in impacts[:5]]
                detail += f" → impacts: {', '.join(impact_names)}"
                if len(impacts) > 5:
                    detail += f" (+{len(impacts) - 5} more)"
            summaries.append(detail)

        return (
            f"Found {len(concepts)} economic concept(s) matching '{query}': "
            f"{'; '.join(summaries)}."
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
    _load_schema()
    sid, session = _get_session(_active_session_id)
    _active_session_id = sid
    bucket = _ensure_entity(session, entity_name, neid)

    try:
        values = _fetch_properties([neid], include_attrs=True)

        bucket.setdefault("properties", []).extend(values)

        unique_pids = list({v.get("pid") for v in values})
        prop_names = sorted({_pname(pid) for pid in unique_pids if _pname(pid)})[:15]

        return (
            f"Found {len(values)} property value(s) for '{entity_name}' "
            f"across {len(unique_pids)} unique properties. "
            f"Properties: {', '.join(prop_names) if prop_names else '(PIDs not in schema)'}."
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

def _fetch_properties(
    eids: list[str], include_attrs: bool = False, timeout: float = 30.0
) -> list[dict]:
    """Fetch property values for a list of entity IDs."""
    data: dict[str, str] = {"eids": json.dumps(eids)}
    if include_attrs:
        data["include_attributes"] = "true"
    resp = elemental_client.post(
        "/elemental/entities/properties", data=data, timeout=timeout
    )
    resp.raise_for_status()
    return resp.json().get("values") or []


def _fetch_properties_batched(
    eids: list[str], include_attrs: bool = False, timeout: float = 10.0, batch_size: int = 5
) -> list[dict]:
    """Fetch properties in small batches to avoid timeouts on mega-entities.

    If a batch times out, individual entities in that batch are tried one
    at a time so a single problematic entity doesn't block the rest.
    """
    all_values: list[dict] = []
    for i in range(0, len(eids), batch_size):
        batch = eids[i : i + batch_size]
        try:
            all_values.extend(_fetch_properties(batch, include_attrs, timeout))
        except Exception:
            for eid in batch:
                try:
                    all_values.extend(_fetch_properties([eid], include_attrs, timeout))
                except Exception:
                    pass
    return all_values


def _extract_ticker(values: list[dict]) -> str | None:
    """Pull the ticker symbol out of a property-value list."""
    for v in values:
        if v.get("pid") in _TICKER_PIDS:
            val = v.get("value")
            if val and isinstance(val, str) and len(val) <= 10:
                return val
    return None


def _find_financial_instrument(ticker: str) -> str | None:
    """Search for a financial_instrument entity by ticker symbol.

    Prefers entities whose name exactly matches the ticker (these are the
    stocks-source entities with actual OHLCV data, as opposed to the larger
    13F-HR merged entities named like "Company Inc.").
    """
    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": ticker, "flavors": ["financial_instrument"]}],
                "maxResults": 5,
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.5,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        matches = resp.json().get("results", [{}])[0].get("matches", [])
        exact = [m for m in matches if m.get("name", "").upper() == ticker.upper()]
        if exact:
            return exact[0]["neid"]
        for m in matches:
            if m.get("flavor") == "financial_instrument":
                return m["neid"]
    except Exception:
        pass
    return None


def _find_organization(name: str) -> str | None:
    """Search for an organization entity by name.

    Tries both the raw name and with common corporate suffixes to improve
    resolution accuracy (e.g. "Netflix" alone may match subsidiaries before
    the parent, while "Netflix Inc" matches the parent directly).
    """
    candidates = [name]
    lower = name.lower().rstrip(".")
    if not any(s in lower for s in ("inc", "corp", "ltd", "llc", "co.", "company")):
        candidates.append(f"{name} Inc")

    for query in candidates:
        try:
            resp = elemental_client.post(
                "/entities/search",
                json={
                    "queries": [{"queryId": 1, "query": query, "flavors": ["organization"]}],
                    "maxResults": 3,
                    "includeNames": True,
                    "includeFlavors": True,
                    "includeScores": True,
                    "minScore": 0.5,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            matches = resp.json().get("results", [{}])[0].get("matches", [])
            if matches and matches[0].get("score", 0) >= 0.8:
                return matches[0]["neid"]
        except Exception:
            pass
    return None


def _find_organization_with_data(name: str) -> str | None:
    """Search for an organization entity that has EDGAR/financial data.

    Prefers organizations with properties like ticker/CIK (EDGAR-sourced)
    over NLP-sourced organizations that only have relationship properties.
    Returns up to 3 candidates and probes each briefly for financial data.
    """
    candidates = [name]
    lower = name.lower().rstrip(".")
    if not any(s in lower for s in ("inc", "corp", "ltd", "llc", "co.", "company")):
        candidates.append(f"{name} Inc")

    all_matches: list[dict] = []
    for query in candidates:
        try:
            resp = elemental_client.post(
                "/entities/search",
                json={
                    "queries": [{"queryId": 1, "query": query, "flavors": ["organization"]}],
                    "maxResults": 5,
                    "includeNames": True,
                    "includeFlavors": True,
                    "includeScores": True,
                    "minScore": 0.5,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            matches = resp.json().get("results", [{}])[0].get("matches", [])
            for m in matches:
                if m.get("score", 0) >= 0.5 and m["neid"] not in {x["neid"] for x in all_matches}:
                    all_matches.append(m)
        except Exception:
            pass

    ticker_pid = _name_to_pid.get("ticker")
    cik_pid = _name_to_pid.get("company_cik")

    for m in all_matches[:5]:
        neid = m["neid"]
        try:
            vals = _fetch_properties([neid], timeout=10.0)
            has_data = any(
                v.get("pid") in (ticker_pid, cik_pid)
                for v in vals
                if v.get("pid")
            )
            if has_data:
                return neid
        except Exception:
            pass

    if all_matches:
        return all_matches[0]["neid"]
    return None


def _get_ticker_for_entity(neid: str) -> str | None:
    """Try to get a ticker by querying the entity or its organization."""
    try:
        values = _fetch_properties([neid], timeout=10.0)
        ticker = _extract_ticker(values)
        if ticker:
            return ticker
    except Exception:
        pass
    try:
        name_resp = elemental_client.get(f"/entities/{neid}/name", timeout=5.0)
        name_resp.raise_for_status()
        entity_name = name_resp.json().get("name", "")
        org_neid = _find_organization(entity_name)
        if org_neid and org_neid != neid:
            org_values = _fetch_properties([org_neid], timeout=10.0)
            return _extract_ticker(org_values)
    except Exception:
        pass
    return None


def _extract_fundamentals(values: list[dict]) -> dict:
    """Extract financial fundamentals from an organization entity."""
    latest: dict[str, tuple[str, any]] = {}
    for v in values:
        pname = _pname(v.get("pid", 0))
        if not pname:
            continue
        recorded = v.get("recorded_at", "")
        if pname in latest:
            if recorded > latest[pname][0]:
                latest[pname] = (recorded, v.get("value"))
        else:
            latest[pname] = (recorded, v.get("value"))

    result: dict[str, any] = {}
    for field in _FILING_FIELDS:
        if field in latest:
            result[field] = latest[field][1]
    return result


def _extract_news(values: list[dict]) -> list[dict]:
    _load_schema()
    title_pid = _name_to_pid.get("title")
    sentiment_pid = _name_to_pid.get("sentiment")
    pub_name_pid = _name_to_pid.get("original_publication_name")
    tone_pid = _name_to_pid.get("tone")

    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        pid = v.get("pid")
        val = v.get("value")
        if pid == title_pid and val:
            by_entity[eid]["title"] = val
        elif pid == sentiment_pid and val is not None:
            try:
                by_entity[eid]["sentiment"] = float(val)
            except (ValueError, TypeError):
                pass
        elif pid == pub_name_pid and val:
            by_entity[eid]["source"] = val
        elif pid == tone_pid and val:
            by_entity[eid]["tone"] = val

        if not by_entity[eid].get("date"):
            recorded = v.get("recorded_at", "")
            if recorded:
                by_entity[eid]["date"] = recorded

    articles = []
    for eid, info in by_entity.items():
        if info.get("title"):
            articles.append({
                "neid": info.get("neid", eid),
                "title": info["title"],
                "date": (info.get("date") or "")[:10],
                "sentiment": info.get("sentiment"),
                "source": info.get("source"),
                "tone": info.get("tone"),
            })
    articles.sort(key=lambda a: a.get("date", ""), reverse=True)
    return articles


def _extract_prices(values: list[dict]) -> list[dict]:
    """Extract OHLCV price data using PID→name mapping."""
    _load_schema()
    price_field_map: dict[int, str] = {}
    for name in _PRICE_FIELDS:
        pid = _name_to_pid.get(name)
        if pid is not None:
            short = name.replace("_price", "").replace("trading_", "")
            price_field_map[pid] = short

    price_points: dict[str, dict] = {}
    ticker = None
    for v in values:
        pid = v.get("pid")
        val = v.get("value")
        if pid in _TICKER_PIDS and val:
            ticker = str(val)
        field = price_field_map.get(pid)
        if not field:
            continue
        recorded = v.get("recorded_at", "")
        date_key = recorded[:10] if recorded else ""
        if not date_key:
            continue
        if date_key not in price_points:
            price_points[date_key] = {"date": date_key}
        price_points[date_key][field] = val

    result = sorted(price_points.values(), key=lambda p: p.get("date", ""), reverse=True)
    for p in result:
        if ticker:
            p["ticker"] = ticker
    return result


def _extract_filings(values: list[dict]) -> list[dict]:
    _load_schema()
    accession_pid = _name_to_pid.get("accession_number")
    form_type_pid = _name_to_pid.get("form_type")
    filing_date_pid = _name_to_pid.get("filing_date")
    report_date_pid = _name_to_pid.get("report_date")
    transaction_type_pid = _name_to_pid.get("transaction_type")
    shares_transacted_pid = _name_to_pid.get("shares_transacted")
    name_pid = 8

    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        pid = v.get("pid")
        val = v.get("value")
        if pid == name_pid and val:
            by_entity[eid]["description"] = str(val)
        elif pid == accession_pid and val:
            by_entity[eid]["accession_number"] = str(val)
        elif pid == form_type_pid and val:
            by_entity[eid]["form_type"] = str(val)
        elif pid == filing_date_pid and val:
            by_entity[eid]["filing_date"] = str(val)
        elif pid == report_date_pid and val:
            by_entity[eid]["report_date"] = str(val)
        elif pid == transaction_type_pid and val:
            by_entity[eid]["transaction_type"] = str(val)
            by_entity[eid].setdefault("form_type", "Form 4")
        elif pid == shares_transacted_pid and val:
            by_entity[eid]["shares_transacted"] = val

        if not by_entity[eid].get("date"):
            recorded = v.get("recorded_at", "")
            if recorded:
                by_entity[eid]["date"] = recorded[:10]

    filings = []
    for eid, info in by_entity.items():
        if not (info.get("accession_number") or info.get("form_type")):
            continue
        entry: dict = {
            "neid": info.get("neid", eid),
            "form_type": info.get("form_type", "Unknown"),
            "date": info.get("filing_date") or info.get("report_date") or info.get("date", "?"),
            "description": info.get("description", ""),
            "accession_number": info.get("accession_number"),
        }
        if info.get("transaction_type"):
            entry["transaction_type"] = info["transaction_type"]
        if info.get("shares_transacted"):
            entry["shares_transacted"] = info["shares_transacted"]
        filings.append(entry)
    filings.sort(key=lambda f: f.get("date", ""), reverse=True)
    return filings


def _extract_events(values: list[dict]) -> list[dict]:
    category_pid = _name_to_pid.get("category")
    event_pid = _name_to_pid.get("form_8k_event")
    event_status_pid = _name_to_pid.get("event_status")
    event_item_pid = _name_to_pid.get("form_8k_item_code")
    likelihood_pid = _name_to_pid.get("likelihood")
    description_pid = _name_to_pid.get("description")
    date_pid = _name_to_pid.get("date")
    alias_pid = _name_to_pid.get("alias")

    by_entity: dict[str, dict] = {}
    for v in values:
        eid = v.get("eid", "")
        if eid not in by_entity:
            by_entity[eid] = {"neid": eid}
        val = v.get("value")
        pid = v.get("pid")
        if pid == alias_pid and val:
            by_entity[eid].setdefault("description", val)
        elif pid == description_pid and val:
            by_entity[eid]["description"] = val
        elif pid == category_pid and val:
            by_entity[eid]["category"] = val
        elif pid == event_pid and val:
            by_entity[eid]["event_type"] = val
        elif pid == event_status_pid and val:
            by_entity[eid]["status"] = val
        elif pid == event_item_pid and val:
            by_entity[eid]["item_code"] = val
        elif pid == likelihood_pid and val:
            by_entity[eid]["likelihood"] = val
        elif pid == date_pid and val:
            by_entity[eid]["date"] = str(val)[:10]

        if not by_entity[eid].get("date"):
            recorded = v.get("recorded_at", "")
            if recorded:
                by_entity[eid]["date"] = recorded[:10]

    events = []
    for _eid, info in by_entity.items():
        if info.get("category") or info.get("description") or info.get("event_type"):
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
