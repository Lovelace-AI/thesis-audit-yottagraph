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


def _error_detail(exc: Exception) -> str:
    """Extract a useful error string, including the response body for HTTP errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:300] if exc.response else ""
        return f"{exc} | body: {body}" if body else str(exc)
    return str(exc)


# ---------------------------------------------------------------------------
# Schema cache — PID ↔ name mapping loaded once from the Elemental API
# ---------------------------------------------------------------------------

_pid_to_name: dict[int, str] = {}
_name_to_pid: dict[str, int] = {}
_schema_loaded = False

_FILING_FIELDS = (
    "accession_number", "form_type", "filing_date", "total_revenue", "net_income",
    "total_assets", "total_liabilities", "shareholders_equity", "shares_outstanding",
    "eps_basic", "eps_diluted",
)
_EVENT_FIELDS = (
    "category", "form_8k_event", "form_8k_item_code", "event_status",
    "likelihood", "description", "date",
)
_NEWS_FIELDS = ("title", "original_publication_name", "tone")

_TICKER_PIDS: set[int] = set()


def _load_schema() -> None:
    """Fetch the KG schema and build PID↔name maps. Idempotent."""
    global _schema_loaded, _TICKER_PIDS
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


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_news(values: list[dict]) -> list[dict]:
    _load_schema()
    title_pid = _name_to_pid.get("title")
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
                "source": info.get("source"),
                "tone": info.get("tone"),
            })
    articles.sort(key=lambda a: a.get("date", ""), reverse=True)
    return articles


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
        values = _fetch_properties([neid], pids=pids, timeout=30.0)
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
        return f"Error fetching properties for '{entity_name}': {_error_detail(e)}", {}


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
        dates = [a["date"] for a in articles if a.get("date")]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        return (
            f"Found {len(articles)} news article(s) for '{entity_name}'. "
            f"Date range: {date_range}."
        ), {"articles": articles}
    except Exception as e:
        return f"Error fetching news for '{entity_name}': {_error_detail(e)}", {}


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
        return f"Error fetching filings for '{entity_name}': {_error_detail(e)}", {}


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
        return f"Error fetching events for '{entity_name}': {_error_detail(e)}", {}


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
        return f"Error fetching relationships for '{entity_name}': {_error_detail(e)}", {}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS = {
    "search_entities": _exec_search_entities,
    "get_properties": _exec_get_properties,
    "get_news": _exec_get_news,
    "get_filings": _exec_get_filings,
    "get_events": _exec_get_events,
    "get_relationships": _exec_get_relationships,
}

_REQUIRED_PARAMS: dict[str, list[str]] = {
    "search_entities": ["query"],
    "get_properties": ["entity_name", "neid"],
    "get_news": ["entity_name", "neid"],
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
    if "neid" in params and params["neid"]:
        params["neid"] = str(params["neid"]).zfill(20)
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

try:
    from researcher.planner_prompt import (
        DEFAULT_OPTIMIZABLE_PROMPT,
        assemble_planner_instruction,
        load_artifact,
    )
except ImportError:
    from .planner_prompt import (
        DEFAULT_OPTIMIZABLE_PROMPT,
        assemble_planner_instruction,
        load_artifact,
    )


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
    """Load planner instruction, preferring planner_prompt.json if present."""
    global _planner_instruction_cache
    if _planner_instruction_cache is not None:
        return _planner_instruction_cache
    from pathlib import Path

    json_file = Path(__file__).parent / "planner_prompt.json"
    if json_file.exists():
        artifact = load_artifact(json_file)
    else:
        artifact = DEFAULT_OPTIMIZABLE_PROMPT
    _planner_instruction_cache = assemble_planner_instruction(artifact)
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
        model="gemini-2.5-flash-lite",
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
_max_iterations: int = 20
DEFAULT_MAX_ITERATIONS = 20


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
        _max_iterations = min(_max_iterations, 30)
        _research_doc = {"query": query_input, "calls": []}
        _full_results = {}
        _call_counter = 0
        _iteration_counter = 0

    _iteration_counter += 1

    if _iteration_counter > _max_iterations:
        result = _build_final_result(
            f"Maximum iterations reached ({_max_iterations}).",
            stop_reason="max_iterations",
        )
        _reset_state()
        return json.dumps(result, default=str)

    prompt = _abridge_research_doc(_research_doc)
    try:
        plan = _call_planner_llm(prompt)
    except Exception as e:
        result = _build_final_result(
            f"Planner error: {e}", stop_reason="planner_error",
        )
        _reset_state()
        return json.dumps(result, default=str)

    if plan.get("action") == "done":
        result = _build_final_result(
            plan.get("reasoning", "Research complete."),
            stop_reason="complete",
        )
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


def _build_final_result(reasoning: str, stop_reason: str = "complete") -> dict:
    """Build the final output with both research doc and full show_your_work."""
    return {
        "status": "done",
        "iteration": _iteration_counter,
        "reasoning": reasoning,
        "stop_reason": stop_reason,
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
        model="gemini-2.5-flash-lite",
        name="researcher",
        instruction=WRAPPER_INSTRUCTION,
        tools=[research_iteration],
    )
except ImportError:
    root_agent = None  # ADK not installed (e.g. research_learner venv)
