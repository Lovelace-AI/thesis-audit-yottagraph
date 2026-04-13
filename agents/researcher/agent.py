"""
Research Agent — planner-executor loop for gathering financial data.

The outer ADK agent calls research_iteration() in a loop. Each call invokes
an inner Gemini planner that sees the growing research doc and requests
batches of API calls. Those calls are executed mechanically, results are
abridged for the planner's context window, and full results are kept in
memory for show_your_work.

Local testing:
    export ELEMENTAL_API_URL=https://query.news.prod.g.lovelace.ai
    export ELEMENTAL_API_TOKEN=<your-token>
    cd agents
    pip install -r researcher/requirements.txt
    adk web
"""

import inspect
import json

import httpx

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
# Internal helpers (shared by executors)
# ---------------------------------------------------------------------------


def _resolve_pids(names: list[str] | None) -> list[int] | None:
    """Convert property names to PIDs. Returns None if names is None (fetch all)."""
    if not names:
        return None
    _load_schema()
    pids = [_name_to_pid[n] for n in names if n in _name_to_pid]
    return pids or None


def _fetch_properties(
    eids: list[str],
    pids: list[int] | None = None,
    include_attrs: bool = False,
    timeout: float = 10.0,
) -> list[dict]:
    """Fetch property values for entity IDs, optionally filtered by PIDs."""
    data: dict[str, str] = {"eids": json.dumps(eids)}
    if pids:
        data["pids"] = json.dumps(pids)
    if include_attrs:
        data["include_attributes"] = "true"
    resp = elemental_client.post(
        "/elemental/entities/properties", data=data, timeout=timeout
    )
    resp.raise_for_status()
    return resp.json().get("values") or []


def _fetch_properties_batched(
    eids: list[str],
    pids: list[int] | None = None,
    include_attrs: bool = False,
    timeout: float = 10.0,
    batch_size: int = 5,
) -> list[dict]:
    """Fetch properties in small batches to avoid timeouts on mega-entities."""
    all_values: list[dict] = []
    for i in range(0, len(eids), batch_size):
        batch = eids[i : i + batch_size]
        try:
            all_values.extend(_fetch_properties(batch, pids, include_attrs, timeout))
        except Exception:
            for eid in batch:
                try:
                    all_values.extend(_fetch_properties([eid], pids, include_attrs, timeout))
                except Exception:
                    pass
    return all_values


def _limit_values_per_pid(values: list[dict], limit: int) -> list[dict]:
    """Keep only the most recent `limit` values per PID (by recorded_at)."""
    by_pid: dict[int, list[dict]] = {}
    for v in values:
        by_pid.setdefault(v.get("pid"), []).append(v)
    result: list[dict] = []
    for pvs in by_pid.values():
        pvs.sort(key=lambda x: x.get("recorded_at", ""), reverse=True)
        result.extend(pvs[:limit])
    return result


def _extract_ticker(values: list[dict]) -> str | None:
    """Pull the ticker symbol out of a property-value list."""
    for v in values:
        if v.get("pid") in _TICKER_PIDS:
            val = v.get("value")
            if val and isinstance(val, str) and len(val) <= 10:
                return val
    return None


def _find_financial_instrument(ticker: str) -> str | None:
    """Search for a financial_instrument entity by ticker symbol."""
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
    """Search for an organization entity by name."""
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
    """Search for an organization entity that has EDGAR/financial data."""
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


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


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
# Executor functions — each returns (summary: str, full_data: dict)
# ---------------------------------------------------------------------------


def _exec_search_entities(
    query: str,
    flavors: list[str] | None = None,
    max_results: int = 5,
) -> tuple[str, dict]:
    """Search for entities by name, with optional flavor filter."""
    try:
        search_query: dict = {"queryId": 1, "query": query}
        if flavors:
            search_query["flavors"] = flavors
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [search_query],
                "maxResults": min(max_results, 20),
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.5,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        matches = resp.json().get("results", [{}])[0].get("matches", [])
        if not matches:
            flavor_str = f" (flavors: {', '.join(flavors)})" if flavors else ""
            return f"No entities found for '{query}'{flavor_str}.", {}

        results = [
            {"neid": m["neid"], "name": m.get("name", ""), "flavor": m.get("flavor", ""), "score": m.get("score", 0)}
            for m in matches
        ]
        names = [r["name"] or r["neid"] for r in results[:5]]
        return (
            f"Found {len(results)} entity match(es) for '{query}': {', '.join(names)}."
        ), {"matches": results}
    except Exception as e:
        return f"Error searching for '{query}': {e}", {}


def _exec_get_properties(
    entity_name: str,
    neid: str,
    properties: list[str] | None = None,
    limit: int = 10,
) -> tuple[str, dict]:
    """Fetch specific named properties for an entity.

    If properties is None, returns all properties (capped by limit per PID).
    """
    _load_schema()
    pids = _resolve_pids(properties)

    try:
        values = _fetch_properties([neid], pids=pids, timeout=10.0)
        if limit and values:
            values = _limit_values_per_pid(values, limit)

        unique_pids = list({v.get("pid") for v in values})
        prop_names = sorted({_pname(pid) for pid in unique_pids if _pname(pid)})

        if properties:
            requested = ", ".join(properties)
            return (
                f"Found {len(values)} value(s) for '{entity_name}' "
                f"(requested: {requested}). "
                f"Properties present: {', '.join(prop_names) if prop_names else 'none'}."
            ), {"properties": values, "property_names": prop_names}

        return (
            f"Found {len(values)} value(s) for '{entity_name}' "
            f"across {len(unique_pids)} properties: "
            f"{', '.join(prop_names[:15]) if prop_names else '(none)'}."
        ), {"properties": values, "property_names": prop_names}
    except Exception as e:
        return f"Error fetching properties for '{entity_name}': {e}", {}


def _exec_get_news(entity_name: str, neid: str, limit: int = 15) -> tuple[str, dict]:
    """Fetch news articles linked to an entity."""
    _load_schema()
    news_pids = _resolve_pids(list(_NEWS_FIELDS))

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "both"},
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": str(min(limit * 2, 60))},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No linked entities found for '{entity_name}'.", {}

        max_eids = min(len(eids), limit + 5)
        values = _fetch_properties_batched(eids[:max_eids], pids=news_pids, timeout=10.0)
        articles = _extract_news(values)

        if not articles:
            return f"No news articles found for '{entity_name}'.", {}

        articles = articles[:limit]
        sentiments = [a["sentiment"] for a in articles if a.get("sentiment") is not None]
        dates = [a["date"] for a in articles if a.get("date")]
        avg_sent = round(sum(sentiments) / len(sentiments), 2) if sentiments else None
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        parts = [f"Found {len(articles)} news article(s) for '{entity_name}'."]
        parts.append(f"Date range: {date_range}.")
        if avg_sent is not None:
            parts.append(f"Average sentiment: {avg_sent} ({len(sentiments)} scored).")
        return " ".join(parts), {"articles": articles}
    except Exception as e:
        return f"Error fetching news for '{entity_name}': {e}", {}


def _get_mcp_bearer_token() -> str | None:
    """Get a bearer token for Lovelace MCP servers (GCP service account only)."""
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        request = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(request, "queryserver:api")
    except Exception:
        return None


def _call_mcp_tool(mcp_url: str, tool_name: str, arguments: dict) -> dict | None:
    """Call an MCP tool and return the parsed result, or None on failure."""
    token = _get_mcp_bearer_token()
    if not token:
        return None
    try:
        resp = httpx.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            return None
        result = resp.json().get("result", {})
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, TypeError):
                    return {"text": item["text"]}
        return result
    except Exception:
        return None


_ELEMENTAL_MCP_URL = "https://mcp.news.prod.g.lovelace.ai/elemental/mcp"
_STOCKS_MCP_URL = "https://mcp.news.prod.g.lovelace.ai/stocks/mcp"


def _fetch_stock_prices_mcp(ticker: str, entity_name: str = "") -> list[dict]:
    """Fetch OHLCV data via Lovelace MCP (avoids mega-entity timeouts)."""
    # Try the stocks MCP first
    for tool_name in ["stocks_get_prices", "get_prices", "get_stock_prices"]:
        data = _call_mcp_tool(
            _STOCKS_MCP_URL, tool_name, {"ticker": ticker, "range": "6m"}
        )
        if data:
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "prices" in data:
                return data["prices"]

    # Try elemental MCP with history
    entity_query = ticker or entity_name
    if entity_query:
        data = _call_mcp_tool(
            _ELEMENTAL_MCP_URL,
            "elemental_get_entity",
            {
                "entity": entity_query,
                "flavor": "financial_instrument",
                "properties": ["close_price", "open_price", "high_price", "low_price", "trading_volume"],
                "history": {"limit": 180},
            },
        )
        if data and isinstance(data, dict):
            hist = data.get("historical_properties", {})
            closes = hist.get("close_price", [])
            if closes:
                prices: list[dict] = []
                for entry in closes:
                    point: dict = {
                        "date": (entry.get("recorded_at") or "")[:10],
                        "close": entry.get("value"),
                    }
                    prices.append(point)
                return prices
    return []


def _exec_get_stock_prices(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch OHLCV stock price data.

    Tries: (1) OHLCV properties with PID filter, (2) MCP via ticker,
    (3) financial_instrument entity search. Much cheaper than the old
    waterfall because each step uses PID-filtered fetches.
    """
    _load_schema()
    price_pids = _resolve_pids(list(_PRICE_FIELDS) + ["ticker_symbol", "ticker"])

    prices: list[dict] = []
    ticker = None

    try:
        values = _fetch_properties([neid], pids=price_pids, timeout=10.0)
        ticker = _extract_ticker(values)
        prices = _extract_prices(values)
    except Exception:
        pass

    if not ticker:
        ticker = _get_ticker_for_entity(neid)

    if not prices and ticker:
        prices = _fetch_stock_prices_mcp(ticker, entity_name)

    if not prices and ticker:
        fi_neid = _find_financial_instrument(ticker)
        if fi_neid and fi_neid != neid:
            try:
                values2 = _fetch_properties([fi_neid], pids=price_pids, timeout=10.0)
                prices = _extract_prices(values2)
                if not ticker:
                    ticker = _extract_ticker(values2)
            except Exception:
                pass

    if not prices:
        hint = " Try get_fundamentals for financial statement data." if ticker else ""
        return f"No OHLCV price data found for '{entity_name}'.{hint}", {}

    full_data: dict = {}
    if ticker:
        full_data["ticker"] = ticker
    full_data["prices"] = prices
    dates = [p["date"] for p in prices if p.get("date")]
    closes = [p.get("close") for p in prices if p.get("close") is not None]
    date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"
    price_range = f"${min(closes):.2f} – ${max(closes):.2f}" if closes else "unknown"

    return (
        f"Found {len(prices)} OHLCV data point(s) for '{entity_name}'"
        + (f" (ticker: {ticker})" if ticker else "")
        + f". Date range: {date_range}. Close price range: {price_range}."
    ), full_data


def _exec_get_fundamentals(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch financial fundamentals (revenue, net income, EPS, etc.) from filings.

    Separate from stock prices — use this for valuation and financial analysis.
    """
    _load_schema()
    fundamental_pids = _resolve_pids(list(_FILING_FIELDS) + ["ticker_symbol", "ticker"])

    try:
        values = _fetch_properties([neid], pids=fundamental_pids, timeout=10.0)
        fundamentals = _extract_fundamentals(values)
        ticker = _extract_ticker(values)

        if not fundamentals:
            org_neid = _find_organization_with_data(entity_name)
            if org_neid and org_neid != neid:
                values2 = _fetch_properties([org_neid], pids=fundamental_pids, timeout=10.0)
                fundamentals = _extract_fundamentals(values2)
                if not ticker:
                    ticker = _extract_ticker(values2)

        if not fundamentals:
            return f"No financial fundamentals found for '{entity_name}'.", {}

        full_data: dict = {"fundamentals": fundamentals}
        if ticker:
            full_data["ticker"] = ticker

        parts = [f"Financial fundamentals for '{entity_name}':"]
        for k, v in list(fundamentals.items())[:8]:
            parts.append(f"{k}={v}")
        return " ".join(parts), full_data
    except Exception as e:
        return f"Error fetching fundamentals for '{entity_name}': {e}", {}


def _exec_get_filings(
    entity_name: str, neid: str, form_types: list[str] | None = None, limit: int = 20,
) -> tuple[str, dict]:
    """Fetch SEC filings, optionally filtered by form type."""
    _load_schema()
    filing_pids = _resolve_pids(list(_FILING_FIELDS) + ["name"])

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "incoming"},
        })
        find_limit = min(limit * 2, 60)
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": str(find_limit)},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No filings found for '{entity_name}'.", {}

        max_eids = min(len(eids), limit + 5)
        values = _fetch_properties_batched(eids[:max_eids], pids=filing_pids, timeout=10.0)
        filings = _extract_filings(values)

        if form_types:
            ft_upper = {ft.upper() for ft in form_types}
            filings = [f for f in filings if f.get("form_type", "").upper() in ft_upper]

        filings = filings[:limit]

        if not filings:
            filter_str = f" (filtered to: {', '.join(form_types)})" if form_types else ""
            return f"No SEC filing data found for '{entity_name}'{filter_str}.", {}

        found_types = list({f.get("form_type", "?") for f in filings})
        dates = [f["date"] for f in filings if f.get("date")]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        return (
            f"Found {len(filings)} filing(s) for '{entity_name}'. "
            f"Form types: {', '.join(found_types)}. Date range: {date_range}."
        ), {"filings": filings}
    except Exception as e:
        return f"Error fetching filings for '{entity_name}': {e}", {}


def _exec_get_events(entity_name: str, neid: str, limit: int = 20) -> tuple[str, dict]:
    """Fetch corporate events linked to an entity."""
    _load_schema()
    event_pids = _resolve_pids(list(_EVENT_FIELDS) + ["alias"])

    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": "incoming"},
        })
        find_limit = min(limit * 2, 60)
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": str(find_limit)},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No events found for '{entity_name}'.", {}

        max_eids = min(len(eids), limit + 5)
        values = _fetch_properties_batched(eids[:max_eids], pids=event_pids, timeout=10.0)
        events = _extract_events(values)
        events = events[:limit]

        if not events:
            return f"No event data found for '{entity_name}'.", {}

        categories = list({e.get("category", "?") for e in events})
        return (
            f"Found {len(events)} event(s) for '{entity_name}'. "
            f"Categories: {', '.join(categories)}."
        ), {"events": events}
    except Exception as e:
        return f"Error fetching events for '{entity_name}': {e}", {}


def _exec_get_relationships(
    entity_name: str, neid: str, direction: str = "both", limit: int = 20,
) -> tuple[str, dict]:
    """Fetch related entities with controllable direction and limit."""
    if direction not in ("incoming", "outgoing", "both"):
        direction = "both"
    try:
        linked_expr = json.dumps({
            "type": "linked",
            "linked": {"to_entity": neid, "distance": 1, "direction": direction},
        })
        resp = elemental_client.post(
            "/elemental/find",
            data={"expression": linked_expr, "limit": str(min(limit * 2, 100))},
        )
        resp.raise_for_status()
        eids = resp.json().get("eids", [])
        if not eids:
            return f"No related entities found for '{entity_name}' (direction={direction}).", {}

        fetch_count = min(len(eids), limit)
        names_resp = elemental_client.post(
            "/entities/names",
            json={"neids": eids[:fetch_count]},
            timeout=10.0,
        )
        names_resp.raise_for_status()
        names_map = names_resp.json().get("results", {})

        relationships = []
        for eid in eids[:fetch_count]:
            name = names_map.get(eid, eid)
            relationships.append({"neid": eid, "name": name})

        related_names = [r["name"] for r in relationships[:10]]
        remaining = len(relationships) - 10 if len(relationships) > 10 else 0
        summary = ", ".join(related_names)
        if remaining:
            summary += f", and {remaining} more"

        return (
            f"Found {len(relationships)} related entities for '{entity_name}': {summary}."
        ), {"relationships": relationships}
    except Exception as e:
        return f"Error fetching relationships for '{entity_name}': {e}", {}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS = {
    "search_entities": _exec_search_entities,
    "get_properties": _exec_get_properties,
    "get_news": _exec_get_news,
    "get_stock_prices": _exec_get_stock_prices,
    "get_fundamentals": _exec_get_fundamentals,
    "get_filings": _exec_get_filings,
    "get_events": _exec_get_events,
    "get_relationships": _exec_get_relationships,
}

_REQUIRED_PARAMS: dict[str, list[str]] = {
    "search_entities": ["query"],
    "get_properties": ["entity_name", "neid"],
    "get_news": ["entity_name", "neid"],
    "get_stock_prices": ["entity_name", "neid"],
    "get_fundamentals": ["entity_name", "neid"],
    "get_filings": ["entity_name", "neid"],
    "get_events": ["entity_name", "neid"],
    "get_relationships": ["entity_name", "neid"],
}


def _dispatch_call(call: dict) -> tuple[str, dict]:
    """Execute a single API call spec. Returns (summary, full_data).

    Filters params to only those accepted by the executor's signature,
    so the planner can freely pass optional params without crashing.
    """
    call_type = call.get("type", "")
    executor = _EXECUTORS.get(call_type)
    if not executor:
        available = ", ".join(_EXECUTORS.keys())
        return f"Unknown call type: {call_type}. Available: {available}", {}
    params = call.get("params", {})
    required = _REQUIRED_PARAMS.get(call_type, [])
    missing = [p for p in required if not params.get(p)]
    if missing:
        return (
            f"Missing required parameter(s) {missing} for {call_type}.",
            {},
        )
    accepted = set(inspect.signature(executor).parameters.keys())
    filtered = {k: v for k, v in params.items() if k in accepted}
    try:
        return executor(**filtered)
    except Exception as e:
        return f"Error: {e}", {}


# ---------------------------------------------------------------------------
# Abridger — keeps research doc within LLM context budget
# ---------------------------------------------------------------------------

_MAX_DOC_SIZE = 500_000
_MAX_PER_RESULT = 20_000


def _abridge_value(value: any, budget: int) -> any:
    """Truncate a value to fit within budget characters."""
    if isinstance(value, str):
        if len(value) <= budget:
            return value
        return value[: budget - 12] + " [truncated]"

    if isinstance(value, list):
        serialized = json.dumps(value, default=str)
        if len(serialized) <= budget:
            return value
        kept = []
        running = 2  # for []
        for item in value:
            item_str = json.dumps(item, default=str)
            if running + len(item_str) + 2 > budget - 40:
                remaining = len(value) - len(kept)
                kept.append(f"... and {remaining} more items")
                break
            kept.append(item)
            running += len(item_str) + 2
        return kept

    if isinstance(value, dict):
        serialized = json.dumps(value, default=str)
        if len(serialized) <= budget:
            return value
        result = {}
        for k, v in value.items():
            per_key = max(50, budget // max(len(value), 1))
            result[k] = _abridge_value(v, per_key)
        return result

    return value


def _abridge_research_doc(doc: dict, max_total: int = _MAX_DOC_SIZE) -> str:
    """Serialize research doc, truncating call results to fit budget."""
    query_json = json.dumps(doc.get("query", {}), default=str)
    overhead = len(query_json) + 200
    available = max_total - overhead

    calls = doc.get("calls", [])
    if not calls:
        return json.dumps(doc, default=str)

    per_result = min(_MAX_PER_RESULT, available // max(len(calls), 1))

    abridged_calls = []
    for call in calls:
        result_str = call.get("result", "")
        abridged = _abridge_value(result_str, per_result)
        abridged_calls.append({**call, "result": abridged})

    return json.dumps(
        {"query": doc.get("query", {}), "calls": abridged_calls}, default=str
    )


# ---------------------------------------------------------------------------
# Planner LLM — calls Gemini directly for structured planning
# ---------------------------------------------------------------------------

PLANNER_INSTRUCTION = """\
You are the planning component of a financial research system. You operate in a loop:

1. You receive a JSON document containing a `query` (thesis, entities, claims, data_needs)
   and a list of `calls` already made with their results.
2. You decide what API calls to make next, OR declare research complete.

## Response format

Return a JSON object with one of these two structures:

### Request more data:
{
    "action": "research",
    "reasoning": "Brief explanation of what you need and why",
    "calls": [
        {"type": "get_news", "params": {"entity_name": "Netflix", "neid": "...", "limit": 10}},
        {"type": "get_stock_prices", "params": {"entity_name": "Disney", "neid": "..."}},
        {"type": "get_properties", "params": {"entity_name": "Apple", "neid": "...", "properties": ["ticker_symbol", "industry", "sector"]}}
    ]
}

### Research complete:
{
    "action": "done",
    "reasoning": "Brief explanation of why you have enough data"
}

## Available API calls

All calls accept optional parameters to control scope and cost. Use them.

### search_entities(query, flavors?, max_results?)
Search for entities by name. Returns matching NEIDs, names, flavors, and match scores.
Use to discover entities not in the original query — e.g. find a company's
financial_instrument entity to get stock data, or find competitors.
- **query** (required): search term (company name, ticker, etc.)
- **flavors** (optional list): filter by entity type, e.g. ["organization", "financial_instrument"]
- **max_results** (optional int, default 5): cap on results

### get_properties(entity_name, neid, properties?, limit?)
Fetch specific named properties for an entity. Much cheaper than a full property dump.
- **entity_name** (required): human-readable name
- **neid** (required): entity NEID
- **properties** (optional list): property names to fetch, e.g. ["ticker_symbol", "industry",
  "sector", "company_cik"]. Omit to fetch all (expensive — avoid when possible).
- **limit** (optional int, default 10): max values per property (for time-series properties)

Common property names: ticker_symbol, ticker, company_cik, industry, sector, market_cap,
exchange, country, description, website, employees, founded_date.

### get_news(entity_name, neid, limit?)
Fetches news articles linked to the entity. Returns article count, date range,
average sentiment score (-1 to 1), and per-article data (title, date, sentiment, source, tone).
- **limit** (optional int, default 15): max articles to return. Use 5-10 for quick context,
  15-20 for deep analysis.

### get_stock_prices(entity_name, neid)
Fetches OHLCV stock price history. Returns daily data (open/high/low/close/volume) with
date range and ticker symbol. If prices aren't available, suggests using get_fundamentals.
Use for price trends, volatility, or correlation analysis.

### get_fundamentals(entity_name, neid)
Fetches financial statement data: total_revenue, net_income, total_assets,
total_liabilities, shareholders_equity, shares_outstanding, eps_basic, eps_diluted.
Separate from stock prices. Use for valuation, financial health, or earnings analysis.
If the entity is a financial_instrument, use search_entities to find the parent organization
first, then call get_fundamentals on the organization NEID.

### get_filings(entity_name, neid, form_types?, limit?)
Fetches SEC filings linked to the entity.
- **form_types** (optional list): filter by form type, e.g. ["10-K", "10-Q"] or ["Form 4"].
  Omit to fetch all types.
- **limit** (optional int, default 20): max filings to return.
Returns form type, filing date, accession number, description. For Form 4 insider trades,
also returns transaction_type and shares_transacted.

### get_events(entity_name, neid, limit?)
Fetches corporate events: mergers, product launches, lawsuits, leadership changes,
regulatory actions, analyst reports. Returns category, description, date, likelihood.
- **limit** (optional int, default 20): max events.

### get_relationships(entity_name, neid, direction?, limit?)
Discovers entities linked to the given entity.
- **direction** (optional, default "both"): "incoming", "outgoing", or "both".
  "incoming" finds entities that link TO this one (filings, events, articles about it).
  "outgoing" finds entities this one links TO (subsidiaries, investors).
- **limit** (optional int, default 20): max related entities to return.

## Strategy

- **Be precise about what you fetch**: Use the `properties` parameter on get_properties
  and `form_types`/`limit` on other calls to avoid overfetching. Every parameter you
  specify reduces cost and latency.
- **Start broad**: First iteration should cover all entities with their primary data needs.
  Match data_needs categories to call types: "market_data" → get_stock_prices,
  "fundamentals" → get_fundamentals, "sentiment" → get_news, "filings" → get_filings,
  "events" → get_events.
- **Use `data_needs`**: The query rewrite identified relevant categories. Cover all of them.
- **Batch calls**: Request multiple calls per iteration. Don't request one at a time.
- **Use search_entities for discovery**: If you need an entity not in query.entities
  (e.g. a financial_instrument for stock prices, or a competitor), use search_entities
  first. Then use the returned NEID in subsequent calls.
- **Follow up on thin results**: If a call returns 0 results, try get_properties with
  a few diagnostic properties (["ticker_symbol", "company_cik", "industry"]) to understand
  what data exists. Or use get_relationships to find related entities.
- **NEIDs are mandatory for entity calls**: get_properties, get_news, get_stock_prices,
  get_fundamentals, get_filings, get_events, get_relationships all require entity_name
  and neid. Use NEIDs from query.entities or from search_entities results.
  Never fabricate a NEID.
- **Don't over-fetch**: 3-4 iterations is typical. Use limits and filters.
- **Error handling**: If a call errors, note it and move on. Never retry the exact same
  call — it will fail the same way. If multiple calls fail for an entity, that entity's
  data is unavailable.
- **Know when to stop**: Say "done" when you have sufficient evidence to address every
  claim in the thesis, or after exhausting useful avenues. The report agent can work
  with partial data.
"""


def _load_broadchurch_config() -> dict:
    """Load broadchurch.yaml for GCP project info."""
    from pathlib import Path

    import yaml

    for candidate in [
        Path("broadchurch.yaml"),
        Path(__file__).parent / "broadchurch.yaml",
    ]:
        if candidate.exists():
            with open(candidate) as f:
                return yaml.safe_load(f) or {}
    return {}


_planner_instruction_cache: str | None = None


def _load_planner_instruction() -> str:
    """Load planner instruction from file if present, else use hardcoded default."""
    global _planner_instruction_cache
    if _planner_instruction_cache is not None:
        return _planner_instruction_cache
    from pathlib import Path

    prompt_file = Path(__file__).parent / "planner_prompt.txt"
    if prompt_file.exists():
        _planner_instruction_cache = prompt_file.read_text().strip()
    else:
        _planner_instruction_cache = PLANNER_INSTRUCTION
    return _planner_instruction_cache


def _call_planner_llm(research_doc_json: str) -> dict:
    """Call Gemini to get the next research plan."""
    from google import genai
    from google.genai import types

    config = _load_broadchurch_config()
    project = config.get("gcp", {}).get("project", "broadchurch")
    region = config.get("gcp", {}).get("region", "us-central1")

    client = genai.Client(vertexai=True, project=project, location=region)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=research_doc_json,
        config=types.GenerateContentConfig(
            system_instruction=_load_planner_instruction(),
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return json.loads(response.text)


# ---------------------------------------------------------------------------
# Research iteration — the single ADK tool that drives the loop
# ---------------------------------------------------------------------------

_research_doc: dict | None = None
_full_results: dict = {}
_call_counter: int = 0
_iteration_counter: int = 0
_max_iterations: int = 5
DEFAULT_MAX_ITERATIONS = 5


def research_iteration(input_json: str = "") -> str:
    """Run one iteration of the research loop.

    First call: provide the query rewrite JSON as input_json.
    Subsequent calls: leave input_json empty (state is internal).

    Returns JSON describing what happened this iteration. When the planner
    decides research is complete, includes the full final output.

    Args:
        input_json: Query rewrite JSON on first call, empty on subsequent calls.

    Returns:
        JSON string with iteration results or final output.
    """
    global _research_doc, _full_results, _call_counter, _iteration_counter, _max_iterations
    _load_schema()

    if _research_doc is None:
        query_input = json.loads(input_json) if input_json else {}
        _max_iterations = query_input.pop("max_iterations", DEFAULT_MAX_ITERATIONS)
        if not isinstance(_max_iterations, int) or _max_iterations < 1:
            _max_iterations = DEFAULT_MAX_ITERATIONS
        _max_iterations = min(_max_iterations, 20)
        _research_doc = {"query": query_input, "calls": []}
        _full_results = {}
        _call_counter = 0
        _iteration_counter = 0

    _iteration_counter += 1

    if _iteration_counter > _max_iterations:
        result = _build_final_result("Maximum iterations reached.")
        _reset_state()
        return json.dumps(result, default=str)

    prompt = _abridge_research_doc(_research_doc)
    try:
        plan = _call_planner_llm(prompt)
    except Exception as e:
        result = _build_final_result(f"Planner error: {e}")
        _reset_state()
        return json.dumps(result, default=str)

    if plan.get("action") == "done":
        result = _build_final_result(plan.get("reasoning", "Research complete."))
        _reset_state()
        return json.dumps(result, default=str)

    calls_made = []
    for call_spec in plan.get("calls", []):
        _call_counter += 1
        call_id = _call_counter
        summary, data = _dispatch_call(call_spec)
        call_record = {
            "id": call_id,
            "type": call_spec["type"],
            "params": call_spec.get("params", {}),
            "status": "ok" if data else "error",
            "result": summary,
        }
        _research_doc["calls"].append(call_record)
        _full_results[call_id] = data
        calls_made.append(call_record)

    return json.dumps(
        {
            "status": "continue",
            "iteration": _iteration_counter,
            "reasoning": plan.get("reasoning", ""),
            "calls_made": calls_made,
        },
        default=str,
    )


def _build_final_result(reasoning: str) -> dict:
    """Build the final output with both research doc and full show_your_work."""
    return {
        "status": "done",
        "iteration": _iteration_counter,
        "reasoning": reasoning,
        "calls_made": [],
        "final": {
            "research": _research_doc,
            "show_your_work": _full_results,
        },
    }


def _reset_state() -> None:
    """Reset module-level state after research completes."""
    global _research_doc, _full_results, _call_counter, _iteration_counter, _max_iterations
    _research_doc = None
    _full_results = {}
    _call_counter = 0
    _iteration_counter = 0
    _max_iterations = DEFAULT_MAX_ITERATIONS


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

WRAPPER_INSTRUCTION = """\
You are a research orchestrator. You receive a research query as JSON.

1. Call research_iteration(input_json=<the user message>) to start.
2. If the response status is "continue", call research_iteration() again
   (no arguments — state is internal).
3. Repeat until the response status is "done".

Do NOT modify the input. Do NOT add commentary. Just call the tool.
"""

try:
    from google.adk.agents import Agent

    root_agent = Agent(
        model="gemini-2.0-flash",
        name="researcher",
        instruction=WRAPPER_INSTRUCTION,
        tools=[research_iteration],
    )
except ImportError:
    root_agent = None  # ADK not installed (e.g. research_learner venv)
