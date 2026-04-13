"""
Research learner — outer optimization loop that iteratively improves the planner instruction.

Each iteration: run research with current prompt → score → record → generate improved prompt.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_learner.db import LearnerDB
from research_learner.fixtures import QUERIES
from research_learner.log import get_logger
from research_learner.runner import run_batch
from research_learner.scorer import DIMENSION_WEIGHTS, ScoreResult, score_research

log = get_logger("learner")

LEARNER_INSTRUCTION = """\
You are a prompt engineer optimizing a structured JSON instruction artifact for a
financial research planner agent. The planner receives a thesis query (entities,
claims, data needs) and a growing research document, then decides which API calls
to make next.

## Artifact structure

The optimizable artifact is a JSON object with exactly these keys:
- `strategy`: high-level research planning guidance
- `skill_filings`: how to use get_filings and get_events
- `skill_fundamentals`: how to use get_fundamentals and get_properties for financials
- `skill_market`: how to use get_stock_prices and related calls
- `skill_news`: how to use get_news
- `skill_discovery`: how to use search_entities, get_relationships, get_properties for discovery

There is a fixed PREAMBLE (role, response format, call signatures) that you CANNOT
see or modify. You only modify the strategy and skill fields above.

## Your task

Given the current artifact JSON and diagnostic information (scores, per-query
scorer reasoning, compact call traces), produce an improved artifact. The goal is
to maximize the weighted total score (0-100).

Each dimension is scored 0-25, then weighted:

| Dimension        | Weight | Max contribution |
|------------------|--------|------------------|
| Coverage         | 0.20   | 20 points        |
| Breadth          | 0.20   | 20 points        |
| Addressability   | 0.50   | 50 points        |
| Efficiency       | 0.10   | 10 points        |

**Addressability is by far the most important dimension.**

## Input

You receive JSON with:
- `current_prompt_json`: the current artifact (strategy + 5 skills)
- `score_history`: recent iterations with scores and change descriptions
- `sub_scores`: per-dimension average scores for the current artifact
- `dimension_weights`: the weight of each dimension in the total score
- `highest_impact_dimension`: dimension with most headroom * weight
- `plateau_detected`: true if scores haven't improved in 3+ iterations
- `per_query_reasoning`: array of {query_key, reasoning} from the scorer per query
- `per_query_call_traces`: array of compact call traces per query (may be truncated)

## Diagnosis rules

Use scorer reasoning and call traces to pinpoint which field to improve:
- Entity lookup failures or poor coverage → `skill_discovery`
- Poor breadth across many queries → `strategy`
- Thin or failed fundamentals retrieval → `skill_fundamentals`
- Thin or failed stock/price data retrieval → `skill_market`
- News coverage gaps or misuse of narrative evidence → `skill_news`
- Filing/event retrieval issues → `skill_filings`
- Overall planning issues (too many iterations, poor batching) → `strategy`

When a `get_properties` call fails, look at the preceding call that motivated it
to determine which skill owns the failure (it could be skill_fundamentals,
skill_market, or skill_discovery depending on context).

### Error pattern analysis

Pay special attention to calls with `"status": "error"` in the call traces.
For each error pattern you observe, consider two types of improvements:

1. **Avoidance**: Can the skill teach the planner to avoid the error entirely?
   For example, if `get_fundamentals` consistently fails on financial_instrument
   entities, the skill might tell the planner to resolve the parent organization
   first. If `get_stock_prices` fails on an organization, the skill might
   suggest searching for the financial_instrument entity instead.

2. **Recovery**: Can the skill teach the planner a better fallback when an error
   occurs? For example, after a failed `get_fundamentals`, try `get_properties`
   with specific financial fields. After a failed `get_stock_prices`, try
   `search_entities` with `flavors: ["financial_instrument"]` for the ticker.

Look for recurring error messages across multiple queries — these indicate
systematic gaps in the current skill guidance rather than one-off data issues.
Update the relevant skill with concrete instructions that would prevent or
recover from the observed errors.

## Rules

1. Usually modify 1-2 fields per iteration. Use `changed_fields` to declare which.
2. If `plateau_detected` is true, consider broader changes across multiple fields.
3. Do NOT change the fundamental architecture (planner decides, code executes).
4. Every field value must be a non-empty string.
5. Call traces may be truncated for token budget — work with what's available.

## Output

Return ONLY a JSON object (no markdown, no explanation):

{
    "prompt_json": {
        "strategy": "...",
        "skill_filings": "...",
        "skill_fundamentals": "...",
        "skill_market": "...",
        "skill_news": "...",
        "skill_discovery": "..."
    },
    "changed_fields": ["skill_market"],
    "change_description": "<1-2 sentences: what you changed and why>"
}
"""

MAX_RETRIES = 3
BACKOFF_SECONDS = [5, 15, 45]


def _get_default_seed() -> str:
    """Return the default optimizable prompt artifact as serialized JSON."""
    try:
        from researcher.planner_prompt import DEFAULT_OPTIMIZABLE_PROMPT
        return json.dumps(DEFAULT_OPTIMIZABLE_PROMPT)
    except ImportError:
        raise RuntimeError(
            "Cannot import researcher.planner_prompt. "
            "Run from the agents/ directory."
        )


def _load_gcp_config() -> tuple[str, str]:
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


_TRACE_RESULT_CAP = 200
_TRACE_TOTAL_BUDGET = 8000


def _build_call_traces(
    db: "LearnerDB", prompt_id: int, queries: dict[str, dict],
) -> list[dict]:
    """Build compact call traces from stored research output for a prompt's runs.

    Truncates result fields and drops lowest-scoring queries' traces if total
    exceeds the token budget.
    """
    runs = db.get_runs_for_prompt(prompt_id)
    scored_traces: list[tuple[int, dict]] = []

    for run in runs:
        raw_output = db.get_run_research_output(run.id)
        if not raw_output:
            continue
        calls = raw_output.get("calls", [])
        compact_calls = []
        for c in calls:
            result_str = str(c.get("result", ""))
            if len(result_str) > _TRACE_RESULT_CAP:
                result_str = result_str[:_TRACE_RESULT_CAP] + "…"
            compact_calls.append({
                "type": c.get("type", ""),
                "status": c.get("status", ""),
                "params": c.get("params", {}),
                "result": result_str,
            })
        scored_traces.append((
            run.score,
            {"query_key": run.query_key, "calls": compact_calls},
        ))

    scored_traces.sort(key=lambda x: x[0], reverse=True)

    result: list[dict] = []
    total_chars = 0
    for _score, trace in scored_traces:
        trace_json = json.dumps(trace, default=str)
        if total_chars + len(trace_json) > _TRACE_TOTAL_BUDGET and result:
            break
        result.append(trace)
        total_chars += len(trace_json)

    return result


def _call_learner_llm(
    current_prompt_json: dict,
    score_history: list[dict],
    sub_scores: dict,
    plateau_detected: bool,
    per_query_reasoning: list[dict],
    per_query_call_traces: list[dict],
) -> dict:
    """Ask the learner LLM to generate an improved prompt artifact."""
    from google import genai
    from google.genai import types

    if sub_scores:
        impact = {
            k: (25 - sub_scores.get(k, 0)) * DIMENSION_WEIGHTS.get(k, 0.25)
            for k in DIMENSION_WEIGHTS
        }
        highest_impact = max(impact, key=lambda k: impact[k])
    else:
        highest_impact = "addressability"

    learner_input = json.dumps({
        "current_prompt_json": current_prompt_json,
        "score_history": score_history,
        "sub_scores": sub_scores,
        "dimension_weights": DIMENSION_WEIGHTS,
        "highest_impact_dimension": highest_impact,
        "plateau_detected": plateau_detected,
        "per_query_reasoning": per_query_reasoning,
        "per_query_call_traces": per_query_call_traces,
    })

    LEARNER_MODEL = "gemini-2.5-flash"
    log.info(
        f"Learner LLM call starting (model={LEARNER_MODEL}, highest_impact={highest_impact}, "
        f"plateau={plateau_detected}, history={len(score_history)} entries, "
        f"artifact {len(json.dumps(current_prompt_json)):,} chars, "
        f"traces={len(per_query_call_traces)})"
    )

    t_client = time.monotonic()
    project, region = _load_gcp_config()
    client = genai.Client(vertexai=True, project=project, location=region)
    client_init_s = time.monotonic() - t_client

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            t_gen = time.monotonic()
            response = client.models.generate_content(
                model=LEARNER_MODEL,
                contents=learner_input,
                config=types.GenerateContentConfig(
                    system_instruction=LEARNER_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.4,
                ),
            )
            result = json.loads(response.text)
            generate_s = time.monotonic() - t_gen

            change = result.get("change_description", "")
            changed = result.get("changed_fields", [])
            new_json = result.get("prompt_json", {})
            log.info(
                f"Learner LLM returned: changed_fields={changed} "
                f"(model={LEARNER_MODEL}, client={client_init_s:.2f}s gen={generate_s:.1f}s)"
            )
            if change:
                log.info(f"Learner change: {change}")
            return result
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning(f"Learner LLM rate limited (model={LEARNER_MODEL}, attempt {attempt+1}), backing off {wait}s: {e}")
                time.sleep(wait)
            else:
                log.error(f"Learner LLM call failed: {e}")
                raise
    raise RuntimeError(f"Learner LLM failed after {MAX_RETRIES} retries: {last_error}")


def _detect_plateau(score_history: list[dict], window: int = 3) -> bool:
    """Return True if the last `window` iterations show no improvement."""
    if len(score_history) < window:
        return False
    recent = score_history[-window:]
    scores = [h["avg_score"] for h in recent if h.get("avg_score") is not None]
    if len(scores) < window:
        return False
    return max(scores) - min(scores) < 2.0


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


def _score_fn_wrapper(query: dict, research_doc: dict) -> dict:
    """Adapter for run_batch: wraps score_research to return a dict."""
    result = score_research(query, research_doc)
    return {
        "score": result.score,
        "coverage": result.coverage,
        "breadth": result.breadth,
        "addressability": result.addressability,
        "efficiency": result.efficiency,
        "reasoning": result.reasoning,
    }


def run_learner(
    iterations: int | None = None,
    hours: float | None = None,
    db_path: Path | None = None,
    seed_prompt: str | None = None,
    force_seed: bool = False,
    query_key: str | None = None,
    max_workers: int = 4,
    cooldown: float = 2.0,
    max_research_iterations: int = 5,
) -> None:
    """Run the learner optimization loop.

    Args:
        iterations: Max number of learner iterations (None = no limit).
        hours: Max wall-clock hours to run (None = no limit).
        db_path: Path to SQLite DB (default: learner.db next to this file).
        seed_prompt: Initial planner instruction (default: researcher's PLANNER_INSTRUCTION).
        force_seed: If True, insert seed even if DB already has prompts.
        query_key: Run against a single query key, or None for all.
        max_workers: Thread pool size for parallel query execution.
        cooldown: Seconds to pause between learner iterations.
        max_research_iterations: Max planner iterations per research run.
    """
    if iterations is None and hours is None:
        raise ValueError("Specify at least one of --iterations or --hours")

    from research_learner.log import LOG_PATH
    from research_learner.report import generate_report

    db = LearnerDB(db_path or Path(__file__).parent / "learner.db")

    # Seed prompt
    if seed_prompt is None:
        seed_prompt = _get_default_seed()

    if db.prompt_count() == 0 or force_seed:
        db.insert_prompt(seed_prompt, parent_id=None, generation=0, change_description="Seed prompt")
        log.info("Inserted seed prompt (id=1)")
    elif db.prompt_count() > 0:
        log.info(f"DB has {db.prompt_count()} existing prompt(s), resuming from latest")

    # Query set
    if query_key:
        if query_key not in QUERIES:
            available = ", ".join(QUERIES.keys())
            raise ValueError(f"Unknown query key '{query_key}'. Available: {available}")
        queries = {query_key: QUERIES[query_key]}
    else:
        queries = dict(QUERIES)

    if not queries:
        raise ValueError("No queries available. Run build_fixtures.py first.")

    log.info(f"Query set: {len(queries)} queries ({', '.join(queries.keys())})")
    log.info(f"Parallel workers: {min(len(queries), max_workers)}")

    # Resume offset
    completed = db.get_completed_iterations()
    if completed > 0:
        log.info(f"Resuming from iteration {completed + 1}")

    # Limits
    max_iters = iterations if iterations is not None else float("inf")
    deadline = time.monotonic() + hours * 3600 if hours is not None else float("inf")
    start_time = time.monotonic()

    iteration_times: list[float] = []
    current_iter = completed

    # Cumulative phase timers
    cum_batch_s = 0.0
    cum_db_s = 0.0
    cum_learner_llm_s = 0.0
    cum_cooldown_s = 0.0

    # Concise startup message on stdout
    limit_parts = []
    if iterations is not None:
        limit_parts.append(f"{iterations} iterations")
    if hours is not None:
        limit_parts.append(f"{hours}h")
    print(f"learner: {len(queries)} queries, {' / '.join(limit_parts)}")
    print(f"log: tail -f {LOG_PATH}")
    log.info(f"=== Learner starting: {' / '.join(limit_parts)} ===")

    try:
        while current_iter < completed + max_iters:
            elapsed = time.monotonic() - start_time

            if hours is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.info(f"Time limit reached ({_format_elapsed(elapsed)} elapsed)")
                    break
                if iteration_times:
                    avg_iter_time = sum(iteration_times) / len(iteration_times)
                    if remaining < avg_iter_time * 0.8:
                        log.info(
                            f"Not enough time for another iteration "
                            f"(~{_format_elapsed(avg_iter_time)} needed, "
                            f"{_format_elapsed(remaining)} remaining)"
                        )
                        break

            current_iter += 1
            iter_start = time.monotonic()

            prompt_rec = db.get_latest_prompt()
            if not prompt_rec:
                raise RuntimeError("No prompts in DB")

            log.info(
                f"--- Iteration {current_iter} starting "
                f"(prompt id={prompt_rec.id}, gen={prompt_rec.generation}) ---"
            )

            # Phase 1: research + scoring batch
            t_batch = time.monotonic()
            results = run_batch(
                queries,
                prompt_rec.prompt_text,
                score_fn=_score_fn_wrapper,
                max_workers=max_workers,
                max_iterations=max_research_iterations,
            )
            batch_s = time.monotonic() - t_batch
            cum_batch_s += batch_s

            # Phase 2: DB writes + aggregation
            t_db = time.monotonic()
            scores = []
            for r in results:
                s = r.score or {"score": 0, "coverage": 0, "breadth": 0, "addressability": 0, "efficiency": 0, "reasoning": ""}
                db.insert_run(
                    prompt_id=prompt_rec.id,
                    query_key=r.query_key,
                    query_json=queries[r.query_key],
                    research_output=r.research.research_doc,
                    score=s["score"],
                    score_coverage=s["coverage"],
                    score_breadth=s["breadth"],
                    score_addressability=s["addressability"],
                    score_efficiency=s["efficiency"],
                    score_reasoning=s.get("reasoning"),
                    iterations_used=r.research.iterations_used,
                    calls_made=r.research.calls_made,
                    errors=r.research.errors,
                )
                scores.append(s["score"])
                log.info(
                    f"Run recorded: query={r.query_key} score={s['score']} "
                    f"iters={r.research.iterations_used} calls={r.research.calls_made} "
                    f"errors={r.research.errors}"
                )

            avg_score = sum(scores) / len(scores) if scores else 0.0
            min_score = min(scores) if scores else 0.0
            max_score = max(scores) if scores else 0.0
            sub = db.get_sub_scores_for_iteration(prompt_rec.id)
            best_ever = db.get_best_score_ever()

            db.insert_learner_iteration(
                iteration_number=current_iter,
                prompt_id=prompt_rec.id,
                avg_score=avg_score,
                min_score=min_score,
                max_score=max_score,
            )
            db_s = time.monotonic() - t_db
            cum_db_s += db_s

            # Phase 3: learner LLM — generate next prompt
            score_history = db.get_recent_score_history(limit=10)
            plateau = _detect_plateau(score_history)
            if plateau:
                log.info("Plateau detected — learner will try a more creative change")

            # Parse current prompt as JSON artifact
            try:
                current_artifact = json.loads(prompt_rec.prompt_text)
            except (json.JSONDecodeError, TypeError):
                current_artifact = None

            from researcher.planner_prompt import (
                DEFAULT_OPTIMIZABLE_PROMPT,
                validate_artifact,
            )

            if not validate_artifact(current_artifact):
                log.warning("Current prompt is not a valid JSON artifact, using defaults")
                current_artifact = dict(DEFAULT_OPTIMIZABLE_PROMPT)

            per_query_reasoning = db.get_per_query_reasoning(prompt_rec.id)
            per_query_traces = _build_call_traces(db, prompt_rec.id, queries)

            t_learner = time.monotonic()
            learner_llm_s = 0.0
            try:
                learner_result = _call_learner_llm(
                    current_prompt_json=current_artifact,
                    score_history=score_history,
                    sub_scores=sub,
                    plateau_detected=plateau,
                    per_query_reasoning=per_query_reasoning,
                    per_query_call_traces=per_query_traces,
                )
                learner_llm_s = time.monotonic() - t_learner
                new_artifact = learner_result.get("prompt_json", {})
                change_desc = learner_result.get("change_description", "")
                changed_fields = learner_result.get("changed_fields", [])

                validated = validate_artifact(new_artifact)
                if validated and json.dumps(validated, sort_keys=True) != json.dumps(current_artifact, sort_keys=True):
                    db.insert_prompt(
                        prompt_text=json.dumps(validated),
                        parent_id=prompt_rec.id,
                        generation=prompt_rec.generation + 1,
                        change_description=change_desc,
                    )
                    log.info(
                        f"New artifact inserted (gen {prompt_rec.generation + 1}, "
                        f"changed={changed_fields}): {change_desc}"
                    )
                elif not validated:
                    log.warning("Learner returned invalid artifact, will retry next iteration")
                else:
                    log.warning("Learner returned unchanged artifact, will retry next iteration")
            except Exception as e:
                learner_llm_s = time.monotonic() - t_learner
                log.error(f"Learner LLM error: {e}")
            cum_learner_llm_s += learner_llm_s

            iter_elapsed = time.monotonic() - iter_start
            iteration_times.append(iter_elapsed)
            total_elapsed = time.monotonic() - start_time

            log.info(
                f"Iteration {current_iter} scores: "
                f"avg={avg_score:.1f} min={min_score:.0f} max={max_score:.0f} "
                f"best_ever={best_ever:.1f} "
                f"cov={sub['coverage']:.0f} brd={sub['breadth']:.0f} "
                f"addr={sub['addressability']:.0f} eff={sub['efficiency']:.0f}"
            )
            log.info(
                f"Iteration {current_iter} timing: "
                f"total={iter_elapsed:.1f}s | "
                f"batch={batch_s:.1f}s db={db_s:.2f}s learner_llm={learner_llm_s:.1f}s "
                f"cooldown={cooldown:.1f}s"
            )

            # Concise stdout: one line per iteration
            limit_str = ""
            if iterations is not None:
                limit_str = f"/{completed + iterations}"
            if hours is not None:
                remaining = deadline - time.monotonic()
                limit_str += f" {_format_elapsed(remaining)} left"
            print(
                f"  #{current_iter}{limit_str}  "
                f"avg={avg_score:.1f}  best={best_ever:.1f}  "
                f"({_format_elapsed(iter_elapsed)})"
            )

            # Phase 4: cooldown
            t_cool = time.monotonic()
            time.sleep(cooldown)
            cum_cooldown_s += time.monotonic() - t_cool

    except KeyboardInterrupt:
        log.info(f"Interrupted at iteration {current_iter}")
        print(f"\nInterrupted at iteration {current_iter}.")

    # Summary
    total_elapsed = time.monotonic() - start_time
    iterations_done = current_iter - completed

    log.info(f"=== Learner finished: {iterations_done} iteration(s) in {_format_elapsed(total_elapsed)} ===")

    if iterations_done > 0:
        avg_iter = sum(iteration_times) / len(iteration_times) if iteration_times else 0
        log.info(
            f"Cumulative timing: "
            f"batch={cum_batch_s:.1f}s ({cum_batch_s/total_elapsed*100:.0f}%) "
            f"db={cum_db_s:.1f}s ({cum_db_s/total_elapsed*100:.0f}%) "
            f"learner_llm={cum_learner_llm_s:.1f}s ({cum_learner_llm_s/total_elapsed*100:.0f}%) "
            f"cooldown={cum_cooldown_s:.1f}s ({cum_cooldown_s/total_elapsed*100:.0f}%)"
        )
        log.info(
            f"Per-iteration avg: "
            f"total={avg_iter:.1f}s "
            f"batch={cum_batch_s/iterations_done:.1f}s "
            f"db={cum_db_s/iterations_done:.2f}s "
            f"learner_llm={cum_learner_llm_s/iterations_done:.1f}s"
        )

    print(f"\n{'='*50}")
    print(f"Done: {iterations_done} iterations in {_format_elapsed(total_elapsed)}")

    best = db.get_best_prompt()
    if best:
        best_avg = db.get_avg_score_for_prompt(best.id)
        print(f"Best prompt: id={best.id} gen={best.generation} avg={best_avg:.1f}")
        log.info(f"Best prompt: id={best.id} gen={best.generation} avg={best_avg:.1f}")

        lineage = db.get_prompt_lineage(best.id)
        if len(lineage) > 1:
            log.info(f"Prompt evolution ({len(lineage)} steps):")
            for p in lineage:
                p_avg = db.get_avg_score_for_prompt(p.id)
                score_str = f"avg={p_avg:.1f}" if p_avg is not None else "not scored"
                desc = p.change_description or "seed"
                log.info(f"  gen {p.generation}: [{score_str}] {desc}")

    try:
        report_path = generate_report(db)
        print(f"Report: {report_path}")
        log.info(f"Report generated: {report_path}")
    except Exception as e:
        log.error(f"Report generation failed: {e}")

    db.close()
