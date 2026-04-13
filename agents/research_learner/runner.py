"""
Research runner — executes the planner-executor loop with a custom planner instruction.

Imports the researcher's dispatcher/abridger for data fetching,
but uses its own Gemini call with a swappable system instruction.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researcher.agent import _abridge_research_doc, _dispatch_call, _load_schema

MAX_RETRIES = 3
BACKOFF_SECONDS = [5, 15, 45]


@dataclass
class ResearchResult:
    research_doc: dict
    iterations_used: int
    calls_made: int
    errors: int
    show_your_work: dict = field(default_factory=dict)


def _load_gcp_config() -> tuple[str, str]:
    """Load GCP project/region from broadchurch.yaml."""
    import yaml

    for candidate in [
        Path("broadchurch.yaml"),
        Path(__file__).resolve().parent.parent / "broadchurch.yaml",
        Path(__file__).resolve().parent.parent.parent / "broadchurch.yaml",
    ]:
        if candidate.exists():
            with open(candidate) as f:
                config = yaml.safe_load(f) or {}
                gcp = config.get("gcp", {})
                return gcp.get("project", "broadchurch"), gcp.get("region", "us-central1")
    return "broadchurch", "us-central1"


def _call_planner(research_doc_json: str, instruction: str) -> dict:
    """Call Gemini with a custom planner instruction. Includes retry with backoff."""
    from google import genai
    from google.genai import types

    project, region = _load_gcp_config()
    client = genai.Client(vertexai=True, project=project, location=region)

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=research_doc_json,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                time.sleep(wait)
            else:
                raise
    raise last_error  # type: ignore[misc]


def _dispatch_with_retry(call_spec: dict) -> tuple[str, dict]:
    """Wrap _dispatch_call with retry for transient HTTP errors."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _dispatch_call(call_spec)
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                time.sleep(wait)
            else:
                return f"Error: {e}", {}
    return f"Error after {MAX_RETRIES} retries: {last_error}", {}


def run_research(
    query: dict,
    instruction: str,
    max_iterations: int = 5,
) -> ResearchResult:
    """Run one full research pass with instance-level state.

    Args:
        query: QueryRewrite JSON dict (thesis, entities, claims, data_needs).
        instruction: The planner system instruction to use.
        max_iterations: Max planner iterations before forcing done.

    Returns:
        ResearchResult with the research doc, stats, and full data.
    """
    _load_schema()

    research_doc: dict = {"query": query, "calls": []}
    full_results: dict = {}
    call_counter = 0
    error_count = 0
    iterations_used = 0

    for iteration in range(1, max_iterations + 1):
        iterations_used = iteration
        prompt = _abridge_research_doc(research_doc)

        try:
            plan = _call_planner(prompt, instruction)
        except Exception as e:
            error_count += 1
            break

        if plan.get("action") == "done":
            break

        for call_spec in plan.get("calls", []):
            call_counter += 1
            summary, data = _dispatch_with_retry(call_spec)
            status = "ok" if data else "error"
            if status == "error":
                error_count += 1
            call_record = {
                "id": call_counter,
                "type": call_spec.get("type", ""),
                "params": call_spec.get("params", {}),
                "status": status,
                "result": summary,
            }
            research_doc["calls"].append(call_record)
            full_results[call_counter] = data

    return ResearchResult(
        research_doc=research_doc,
        iterations_used=iterations_used,
        calls_made=call_counter,
        errors=error_count,
        show_your_work=full_results,
    )


@dataclass
class BatchRunResult:
    query_key: str
    research: ResearchResult
    score: dict | None = None


def run_batch(
    queries: dict[str, dict],
    instruction: str,
    score_fn=None,
    max_workers: int = 4,
    max_iterations: int = 5,
) -> list[BatchRunResult]:
    """Run research + optional scoring for multiple queries in parallel.

    Args:
        queries: Dict of query_key -> QueryRewrite JSON.
        instruction: Planner instruction to use for all queries.
        score_fn: Optional callable(query_dict, research_doc) -> score_dict.
        max_workers: Thread pool size.
        max_iterations: Max planner iterations per query.

    Returns:
        List of BatchRunResult with research and optional score for each query.
    """
    workers = min(len(queries), max_workers)

    def _run_one(key: str, query: dict) -> BatchRunResult:
        research = run_research(query, instruction, max_iterations)
        score = None
        if score_fn:
            try:
                score = score_fn(query, research.research_doc)
            except Exception as e:
                score = {
                    "score": 0,
                    "coverage": 0,
                    "breadth": 0,
                    "addressability": 0,
                    "efficiency": 0,
                    "reasoning": f"Scoring failed: {e}",
                }
        return BatchRunResult(query_key=key, research=research, score=score)

    results: list[BatchRunResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, key, query): key
            for key, query in queries.items()
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r.query_key)
    return results
