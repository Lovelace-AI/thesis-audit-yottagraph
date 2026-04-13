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
You are a prompt engineer optimizing a system instruction for a financial research
planner agent. The planner receives a thesis query (entities, claims, data needs)
and a growing research document, then decides which API calls to make next.

## Your task

Given the current planner instruction and its recent performance scores, produce
an improved version. The goal is to maximize the weighted total score (0-100).

Each dimension is scored 0-25, then weighted to compute the total:

| Dimension        | Weight | Max contribution |
|------------------|--------|------------------|
| Coverage         | 0.20   | 20 points        |
| Breadth          | 0.20   | 20 points        |
| Addressability   | 0.50   | 50 points        |
| Efficiency       | 0.10   | 10 points        |

**Addressability is by far the most important dimension.** The research must
gather data that directly helps evaluate each claim in the thesis. Coverage and
breadth support this, but are secondary. Efficiency is a tiebreaker.

## Input

You receive JSON with:
- `current_prompt`: the full text of the current planner instruction
- `score_history`: recent iterations with scores and change descriptions
- `sub_scores`: per-dimension average scores for the current prompt
- `dimension_weights`: the weight of each dimension in the total score
- `highest_impact_dimension`: dimension where improvement would most increase
  the weighted total (accounts for both headroom and weight)
- `plateau_detected`: true if scores haven't improved in 3+ iterations

## Rules

1. Make **incremental** changes — 1-2 targeted edits per iteration, not wholesale rewrites.
2. You MUST preserve the JSON response format specification. The planner must still
   return `{"action": "research"|"done", "reasoning": "...", "calls": [...]}`.
3. Focus on the **highest_impact_dimension** — this accounts for both the current
   score gap and the dimension's weight. A small improvement to addressability
   (weight 0.50) is worth more than a large improvement to efficiency (weight 0.10).
4. If `plateau_detected` is true, try a more creative or structural change.
5. Do NOT remove information about available API calls or their parameters.
6. Do NOT change the fundamental architecture (planner decides, code executes).

## Output

Return ONLY a JSON object (no markdown, no explanation):

{
    "prompt": "<the full modified planner instruction>",
    "change_description": "<1-2 sentences: what you changed and why>"
}
"""

MAX_RETRIES = 3
BACKOFF_SECONDS = [5, 15, 45]


def _get_default_seed() -> str:
    """Import the researcher's PLANNER_INSTRUCTION as the default seed."""
    try:
        from researcher.agent import PLANNER_INSTRUCTION
        return PLANNER_INSTRUCTION
    except ImportError:
        raise RuntimeError(
            "Cannot import researcher.agent.PLANNER_INSTRUCTION. "
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


def _call_learner_llm(
    current_prompt: str,
    score_history: list[dict],
    sub_scores: dict,
    plateau_detected: bool,
) -> dict:
    """Ask the learner LLM to generate an improved planner instruction."""
    from google import genai
    from google.genai import types

    # Highest-impact dimension: where (headroom * weight) is greatest
    if sub_scores:
        impact = {
            k: (25 - sub_scores.get(k, 0)) * DIMENSION_WEIGHTS.get(k, 0.25)
            for k in DIMENSION_WEIGHTS
        }
        highest_impact = max(impact, key=lambda k: impact[k])
    else:
        highest_impact = "addressability"

    learner_input = json.dumps({
        "current_prompt": current_prompt,
        "score_history": score_history,
        "sub_scores": sub_scores,
        "dimension_weights": DIMENSION_WEIGHTS,
        "highest_impact_dimension": highest_impact,
        "plateau_detected": plateau_detected,
    })

    LEARNER_MODEL = "gemini-3.1-flash"
    log.info(
        f"Learner LLM call starting (model={LEARNER_MODEL}, highest_impact={highest_impact}, "
        f"plateau={plateau_detected}, history={len(score_history)} entries, "
        f"prompt {len(current_prompt):,} chars)"
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
            new_len = len(result.get("prompt", ""))
            log.info(
                f"Learner LLM returned: new prompt {new_len:,} chars "
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

            t_learner = time.monotonic()
            learner_llm_s = 0.0
            try:
                learner_result = _call_learner_llm(
                    current_prompt=prompt_rec.prompt_text,
                    score_history=score_history,
                    sub_scores=sub,
                    plateau_detected=plateau,
                )
                learner_llm_s = time.monotonic() - t_learner
                new_prompt = learner_result.get("prompt", "")
                change_desc = learner_result.get("change_description", "")

                if new_prompt and new_prompt != prompt_rec.prompt_text:
                    db.insert_prompt(
                        prompt_text=new_prompt,
                        parent_id=prompt_rec.id,
                        generation=prompt_rec.generation + 1,
                        change_description=change_desc,
                    )
                    log.info(f"New prompt inserted (gen {prompt_rec.generation + 1}): {change_desc}")
                else:
                    log.warning("Learner returned unchanged prompt, will retry next iteration")
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
