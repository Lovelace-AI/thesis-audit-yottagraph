"""
Research Agent — planner-executor loop for gathering financial data.

The outer ADK agent calls research_iteration() in a loop. Each call invokes
an inner Gemini planner that sees the growing research doc and requests
batches of API calls. Those calls are executed mechanically, results are
abridged for the planner's context window, and full results are kept in
memory for show_your_work.

Local testing:
    export ELEMENTAL_API_URL=https://stable-query.lovelace.ai
    export ELEMENTAL_API_TOKEN=<your-token>
    cd agents
    pip install -r researcher/requirements.txt
    adk web
"""

import json

import httpx
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
# Internal helpers (shared by executors)
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
    """Fetch properties in small batches to avoid timeouts on mega-entities."""
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


def _exec_get_news(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch news. Returns (summary_string, full articles dict)."""
    _load_schema()

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
            return f"No linked entities found for '{entity_name}'.", {}

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
        articles = _extract_news(values)

        if not articles:
            return f"No news articles found for '{entity_name}'.", {}

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
    """Fetch OHLCV data or financial fundamentals."""
    _load_schema()

    prices: list[dict] = []
    ticker = None
    fundamentals: dict = {}

    # Strategy 0: resolve ticker first, then try MCP (avoids mega-entity timeouts)
    try:
        values = _fetch_properties([neid], timeout=15.0)
        ticker = _extract_ticker(values)
        prices = _extract_prices(values)
    except Exception:
        pass

    if not ticker:
        ticker = _get_ticker_for_entity(neid)

    if not prices and ticker:
        prices = _fetch_stock_prices_mcp(ticker)

    # Strategy 1: find FI entity and fetch its properties
    if not prices:
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

    # Strategy 2: fall back to fundamentals from the organization
    if not prices:
        org_neid = _find_organization_with_data(entity_name)
        if org_neid:
            try:
                values3 = _fetch_properties([org_neid], timeout=15.0)
                fundamentals = _extract_fundamentals(values3)
                if not ticker:
                    ticker = _extract_ticker(values3)
            except Exception:
                pass

    if not prices and not fundamentals:
        return f"No stock price data found for '{entity_name}'.", {}

    full_data: dict = {}
    parts = []

    if ticker:
        full_data["ticker"] = ticker
    if prices:
        full_data["prices"] = prices
        dates = [p["date"] for p in prices if p.get("date")]
        closes = [p.get("close") for p in prices if p.get("close") is not None]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"
        price_range = f"${min(closes):.2f} – ${max(closes):.2f}" if closes else "unknown"
        parts.append(
            f"Found {len(prices)} OHLCV data point(s) for '{entity_name}'"
            + (f" (ticker: {ticker})" if ticker else "")
            + f". Date range: {date_range}. Close price range: {price_range}."
        )
    if fundamentals:
        full_data["fundamentals"] = fundamentals
        parts.append(
            f"Financial fundamentals: revenue=${fundamentals.get('total_revenue', '?')}, "
            f"net_income=${fundamentals.get('net_income', '?')}, "
            f"shares_outstanding={fundamentals.get('shares_outstanding', '?')}."
        )

    return " ".join(parts), full_data


def _exec_get_filings(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch SEC filings."""
    _load_schema()

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
            return f"No filings found for '{entity_name}'.", {}

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
        filings = _extract_filings(values)

        if not filings:
            return f"No SEC filing data found for '{entity_name}'.", {}

        form_types = list({f.get("form_type", "?") for f in filings})
        dates = [f["date"] for f in filings if f.get("date")]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        return (
            f"Found {len(filings)} filing(s) for '{entity_name}'. "
            f"Form types: {', '.join(form_types)}. Date range: {date_range}."
        ), {"filings": filings}
    except Exception as e:
        return f"Error fetching filings for '{entity_name}': {e}", {}


def _exec_get_events(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch events."""
    _load_schema()

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
            return f"No events found for '{entity_name}'.", {}

        values = _fetch_properties_batched(eids[:20], timeout=10.0)
        events = _extract_events(values)

        if not events:
            return f"No event data found for '{entity_name}'.", {}

        categories = list({e.get("category", "?") for e in events})
        return (
            f"Found {len(events)} event(s) for '{entity_name}'. "
            f"Categories: {', '.join(categories)}."
        ), {"events": events}
    except Exception as e:
        return f"Error fetching events for '{entity_name}': {e}", {}


def _exec_get_relationships(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch related entities."""
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
            return f"No related entities found for '{entity_name}'.", {}

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


def _exec_get_entity_properties(entity_name: str, neid: str) -> tuple[str, dict]:
    """Fetch all properties for an entity."""
    _load_schema()

    try:
        values = _fetch_properties([neid], include_attrs=True)
        unique_pids = list({v.get("pid") for v in values})
        prop_names = sorted({_pname(pid) for pid in unique_pids if _pname(pid)})[:15]

        return (
            f"Found {len(values)} property value(s) for '{entity_name}' "
            f"across {len(unique_pids)} unique properties. "
            f"Properties: {', '.join(prop_names) if prop_names else '(PIDs not in schema)'}."
        ), {"properties": values, "property_names": prop_names}
    except Exception as e:
        return f"Error fetching properties for '{entity_name}': {e}", {}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS = {
    "get_news": _exec_get_news,
    "get_stock_prices": _exec_get_stock_prices,
    "get_filings": _exec_get_filings,
    "get_events": _exec_get_events,
    "get_relationships": _exec_get_relationships,
    "get_entity_properties": _exec_get_entity_properties,
}


_REQUIRED_PARAMS: dict[str, list[str]] = {
    "get_news": ["entity_name", "neid"],
    "get_stock_prices": ["entity_name", "neid"],
    "get_filings": ["entity_name", "neid"],
    "get_events": ["entity_name", "neid"],
    "get_relationships": ["entity_name", "neid"],
    "get_entity_properties": ["entity_name", "neid"],
}


def _dispatch_call(call: dict) -> tuple[str, dict]:
    """Execute a single API call spec. Returns (summary, full_data)."""
    call_type = call.get("type", "")
    executor = _EXECUTORS.get(call_type)
    if not executor:
        return f"Unknown call type: {call_type}", {}
    params = call.get("params", {})
    required = _REQUIRED_PARAMS.get(call_type, [])
    missing = [p for p in required if not params.get(p)]
    if missing:
        return (
            f"Missing required parameter(s) {missing} for {call_type}. "
            f"All calls require entity_name and neid from query.entities.",
            {},
        )
    try:
        return executor(**params)
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
        {"type": "get_news", "params": {"entity_name": "Netflix", "neid": "07456007231444618110"}},
        {"type": "get_stock_prices", "params": {"entity_name": "Disney", "neid": "..."}}
    ]
}

### Research complete:
{
    "action": "done",
    "reasoning": "Brief explanation of why you have enough data"
}

## Available API calls

### get_news(entity_name, neid)
Fetches recent news articles linked to the entity. Returns article count, date range,
average sentiment score (-1 to 1), and per-article data (title, date, sentiment, source,
tone). Use for sentiment context, recent developments, or market reaction.
Typically returns 5-20 articles.

### get_stock_prices(entity_name, neid)
Fetches OHLCV stock price history or financial fundamentals. For most companies returns
daily price data (open/high/low/close/volume) with date ranges and ticker symbol. For very
large entities (mega-cap stocks), OHLCV may time out and fall back to financial fundamentals
(revenue, net income, EPS, shares outstanding) from SEC filings.
Always returns ticker when available. Use for any price-related or valuation claim.

### get_filings(entity_name, neid)
Fetches SEC filings: 10-K, 10-Q, 8-K, Form 4, SC 13G, etc. Returns form type, filing date,
accession number, description. For Form 4 (insider trading), also returns transaction type
and shares transacted. Use for corporate governance, insider activity, or financial reporting.

### get_events(entity_name, neid)
Fetches corporate events: mergers & acquisitions, product launches, lawsuits, leadership
changes, regulatory actions, analyst reports. Returns event category, description, date,
likelihood. Use for catalysts, corporate actions, or market-moving events.

### get_relationships(entity_name, neid)
Discovers entities linked to the given entity (competitors, subsidiaries, investors,
partners). Returns a list of related entity names and NEIDs. Use to find additional
entities worth investigating — e.g. find competitors for industry-trend theses.

### get_entity_properties(entity_name, neid)
Raw property dump for an entity. Returns property count and names (e.g. ticker,
company_cik, industry, sector). Use as diagnostic/exploration when other calls don't
return expected data, or to understand what data is available for an unfamiliar entity.

## Strategy

- **Start broad**: First iteration should cover all entities with their primary data needs
  (news + stock prices for most financial theses).
- **Use `data_needs`**: The query rewrite identified relevant categories. Cover all of them.
- **Batch calls**: Request multiple calls per iteration. Don't request one at a time.
- **Follow up on thin results**: If a call returns 0 results or errors, try
  `get_relationships` to find related entities, or `get_entity_properties` to understand
  what exists.
- **NEIDs are mandatory**: Every call requires both `entity_name` and `neid`. You can
  ONLY use NEIDs from `query.entities`. Never omit the `neid` param, never fabricate one.
  If an entity isn't in the query, you cannot fetch data for it directly — use
  `get_relationships` on a known entity to discover related NEIDs first.
- **Don't over-fetch**: 3-4 iterations is typical. If you have news, prices, and events
  for all entities in the query, that's usually enough.
- **Error handling**: If a call errors, note it and move on. Never retry the exact same
  call with the same params — it will fail the same way. If multiple calls fail for the
  same entity (e.g. invalid NEID, 400 errors), that entity's data is unavailable — do
  NOT keep retrying.
- **Know when to stop**: If you've collected data for all entities that have valid NEIDs
  and further calls keep failing, say "done" with what you have. The report agent can work
  with partial data. Spinning on unresolvable errors wastes iterations.
- **Say "done"** when you have sufficient evidence to address every claim in the thesis,
  or after exhausting useful avenues.
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
            system_instruction=PLANNER_INSTRUCTION,
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

root_agent = Agent(
    model="gemini-2.0-flash",
    name="researcher",
    instruction=WRAPPER_INSTRUCTION,
    tools=[research_iteration],
)
