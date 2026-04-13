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

from research_learner.log import get_logger

log = get_logger("runner")

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


@dataclass
class _TimedLLMResult:
    """Wraps an LLM result with timing metadata for the caller to aggregate."""

    result: dict
    client_init_s: float
    generate_s: float


PLANNER_MODEL = "gemini-2.5-pro"


def _call_planner(research_doc_json: str, instruction: str) -> _TimedLLMResult:
    """Call Gemini with a custom planner instruction. Includes retry with backoff."""
    from google import genai
    from google.genai import types

    doc_len = len(research_doc_json)
    log.info(f"Planner LLM call starting (model={PLANNER_MODEL}, input {doc_len:,} chars)")

    t_client = time.monotonic()
    project, region = _load_gcp_config()
    client = genai.Client(vertexai=True, project=project, location=region)
    client_init_s = time.monotonic() - t_client

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            t_gen = time.monotonic()
            response = client.models.generate_content(
                model=PLANNER_MODEL,
                contents=research_doc_json,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            result = json.loads(response.text)
            generate_s = time.monotonic() - t_gen

            action = result.get("action", "?")
            n_calls = len(result.get("calls", []))
            log.info(
                f"Planner LLM returned action={action} calls={n_calls} "
                f"(model={PLANNER_MODEL}, client={client_init_s:.2f}s gen={generate_s:.1f}s)"
            )
            if result.get("reasoning"):
                log.debug(f"Planner LLM reasoning: {result['reasoning']}")
            return _TimedLLMResult(
                result=result,
                client_init_s=client_init_s,
                generate_s=generate_s,
            )
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning(f"Planner LLM rate limited (attempt {attempt+1}), backing off {wait}s: {e}")
                time.sleep(wait)
            else:
                log.error(f"Planner LLM call failed: {e}")
                raise
    raise last_error  # type: ignore[misc]


def _dispatch_with_retry(call_spec: dict) -> tuple[str, dict]:
    """Wrap _dispatch_call with retry for transient HTTP errors."""
    call_type = call_spec.get("type", "?")
    params = call_spec.get("params", {})
    entity = params.get("entity_id") or params.get("neid") or ""
    label = f"{call_type}"
    if entity:
        label += f"({entity})"

    log.info(f"API call starting: {label}")
    t0 = time.monotonic()

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            summary, data = _dispatch_call(call_spec)
            elapsed = time.monotonic() - t0
            ok = bool(data)
            log.info(f"API call returned: {label} ok={ok} ({elapsed:.1f}s)")
            if not ok:
                log.debug(f"API call error detail: {label} -> {summary[:200]}")
            return summary, data
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning(f"API call {label} rate limited (attempt {attempt+1}), backing off {wait}s")
                time.sleep(wait)
            else:
                elapsed = time.monotonic() - t0
                log.error(f"API call failed: {label} ({elapsed:.1f}s): {e}")
                return f"Error: {e}", {}
    elapsed = time.monotonic() - t0
    log.error(f"API call exhausted retries: {label} ({elapsed:.1f}s): {last_error}")
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
    thesis = query.get("thesis", "?")[:80]
    log.info(f"Research starting: thesis={thesis!r} max_iter={max_iterations}")
    t0 = time.monotonic()

    _load_schema()

    research_doc: dict = {"query": query, "calls": []}
    full_results: dict = {}
    call_counter = 0
    error_count = 0
    iterations_used = 0

    planner_client_s = 0.0
    planner_gen_s = 0.0
    api_dispatch_s = 0.0

    for iteration in range(1, max_iterations + 1):
        iterations_used = iteration
        log.debug(f"Research iteration {iteration}/{max_iterations}")
        prompt = _abridge_research_doc(research_doc)

        try:
            timed = _call_planner(prompt, instruction)
            plan = timed.result
            planner_client_s += timed.client_init_s
            planner_gen_s += timed.generate_s
        except Exception as e:
            log.error(f"Planner LLM error on iteration {iteration}, aborting research: {e}")
            error_count += 1
            break

        if plan.get("action") == "done":
            log.info(f"Planner LLM signalled done on iteration {iteration}")
            break

        for call_spec in plan.get("calls", []):
            call_counter += 1
            t_api = time.monotonic()
            summary, data = _dispatch_with_retry(call_spec)
            api_dispatch_s += time.monotonic() - t_api
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

    elapsed = time.monotonic() - t0
    other_s = elapsed - planner_client_s - planner_gen_s - api_dispatch_s
    log.info(
        f"Research finished: thesis={thesis!r} "
        f"iters={iterations_used} calls={call_counter} errors={error_count} ({elapsed:.1f}s) | "
        f"planner_llm_client={planner_client_s:.2f}s planner_llm_gen={planner_gen_s:.1f}s "
        f"api={api_dispatch_s:.1f}s other={other_s:.2f}s"
    )

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
    log.info(f"Batch starting: {len(queries)} queries, {workers} workers")
    batch_t0 = time.monotonic()

    def _run_one(key: str, query: dict) -> BatchRunResult:
        log.info(f"Batch query [{key}] starting")
        q_t0 = time.monotonic()

        t_research = time.monotonic()
        research = run_research(query, instruction, max_iterations)
        research_s = time.monotonic() - t_research

        score = None
        score_s = 0.0
        if score_fn:
            try:
                log.info(f"Batch query [{key}] scoring starting")
                t_score = time.monotonic()
                score = score_fn(query, research.research_doc)
                score_s = time.monotonic() - t_score
                log.info(f"Batch query [{key}] scored: {score.get('score', '?')} ({score_s:.1f}s)")
            except Exception as e:
                log.error(f"Batch query [{key}] scoring failed: {e}")
                score = {
                    "score": 0,
                    "coverage": 0,
                    "breadth": 0,
                    "addressability": 0,
                    "efficiency": 0,
                    "reasoning": f"Scoring failed: {e}",
                }

        q_elapsed = time.monotonic() - q_t0
        log.info(
            f"Batch query [{key}] complete ({q_elapsed:.1f}s) | "
            f"research={research_s:.1f}s scoring={score_s:.1f}s"
        )
        return BatchRunResult(query_key=key, research=research, score=score)

    results: list[BatchRunResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, key, query): key
            for key, query in queries.items()
        }
        for future in as_completed(futures):
            results.append(future.result())

    batch_elapsed = time.monotonic() - batch_t0
    log.info(f"Batch complete: {len(results)} results ({batch_elapsed:.1f}s)")

    results.sort(key=lambda r: r.query_key)
    return results
