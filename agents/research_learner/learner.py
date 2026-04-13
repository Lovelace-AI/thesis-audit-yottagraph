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
from research_learner.runner import run_batch
from research_learner.scorer import ScoreResult, score_research

LEARNER_INSTRUCTION = """\
You are a prompt engineer optimizing a system instruction for a financial research
planner agent. The planner receives a thesis query (entities, claims, data needs)
and a growing research document, then decides which API calls to make next.

## Your task

Given the current planner instruction and its recent performance scores, produce
an improved version. The goal is to maximize the total score (0-100) across four
dimensions:

- **Coverage** (0-25): Did research cover all entities in the query?
- **Breadth** (0-25): Were all relevant data types fetched per data_needs?
- **Addressability** (0-25): Does the gathered data help evaluate each claim?
- **Efficiency** (0-25): Was research completed without wasted calls?

## Input

You receive JSON with:
- `current_prompt`: the full text of the current planner instruction
- `score_history`: recent iterations with scores and change descriptions
- `sub_scores`: per-dimension average scores for the current prompt
- `weakest_dimension`: which dimension scored lowest
- `plateau_detected`: true if scores haven't improved in 3+ iterations

## Rules

1. Make **incremental** changes — 1-2 targeted edits per iteration, not wholesale rewrites.
2. You MUST preserve the JSON response format specification. The planner must still
   return `{"action": "research"|"done", "reasoning": "...", "calls": [...]}`.
3. Focus on the **weakest dimension** unless it's already near max.
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

    project, region = _load_gcp_config()
    client = genai.Client(vertexai=True, project=project, location=region)

    weakest = min(sub_scores, key=lambda k: sub_scores[k]) if sub_scores else "coverage"

    learner_input = json.dumps({
        "current_prompt": current_prompt,
        "score_history": score_history,
        "sub_scores": sub_scores,
        "weakest_dimension": weakest,
        "plateau_detected": plateau_detected,
    })

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=learner_input,
                config=types.GenerateContentConfig(
                    system_instruction=LEARNER_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.4,
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

    from research_learner.report import generate_report

    db = LearnerDB(db_path or Path(__file__).parent / "learner.db")

    # Seed prompt
    if seed_prompt is None:
        seed_prompt = _get_default_seed()

    if db.prompt_count() == 0 or force_seed:
        db.insert_prompt(seed_prompt, parent_id=None, generation=0, change_description="Seed prompt")
        print("Inserted seed prompt (id=1)")
    elif db.prompt_count() > 0:
        print(f"DB already has {db.prompt_count()} prompt(s), resuming from latest.")

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

    print(f"Running against {len(queries)} query(ies): {', '.join(queries.keys())}")
    print(f"Parallel workers: {min(len(queries), max_workers)}")

    # Resume offset
    completed = db.get_completed_iterations()
    if completed > 0:
        print(f"Resuming from iteration {completed + 1}")

    # Limits
    max_iters = iterations if iterations is not None else float("inf")
    deadline = time.monotonic() + hours * 3600 if hours is not None else float("inf")
    start_time = time.monotonic()

    iteration_times: list[float] = []
    current_iter = completed

    try:
        while current_iter < completed + max_iters:
            elapsed = time.monotonic() - start_time

            # Check time limit, estimating if we can fit another iteration
            if hours is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print(f"\nTime limit reached ({_format_elapsed(elapsed)} elapsed).")
                    break
                if iteration_times:
                    avg_iter_time = sum(iteration_times) / len(iteration_times)
                    if remaining < avg_iter_time * 0.8:
                        print(f"\nNot enough time for another iteration (~{_format_elapsed(avg_iter_time)} needed, {_format_elapsed(remaining)} remaining).")
                        break

            current_iter += 1
            iter_start = time.monotonic()

            # Load current prompt
            prompt_rec = db.get_latest_prompt()
            if not prompt_rec:
                raise RuntimeError("No prompts in DB")

            # Run research + scoring in parallel
            results = run_batch(
                queries,
                prompt_rec.prompt_text,
                score_fn=_score_fn_wrapper,
                max_workers=max_workers,
                max_iterations=max_research_iterations,
            )

            # Record runs
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

            # Aggregate
            avg_score = sum(scores) / len(scores) if scores else 0.0
            min_score = min(scores) if scores else 0.0
            max_score = max(scores) if scores else 0.0
            sub = db.get_sub_scores_for_iteration(prompt_rec.id)
            best_ever = db.get_best_score_ever()

            # Record learner iteration
            db.insert_learner_iteration(
                iteration_number=current_iter,
                prompt_id=prompt_rec.id,
                avg_score=avg_score,
                min_score=min_score,
                max_score=max_score,
            )

            iter_elapsed = time.monotonic() - iter_start
            iteration_times.append(iter_elapsed)
            total_elapsed = time.monotonic() - start_time

            # Progress output
            limit_str = ""
            if iterations is not None:
                limit_str = f"/{completed + iterations}"
            if hours is not None:
                remaining = deadline - time.monotonic()
                limit_str += f" ({_format_elapsed(remaining)} left)"

            print(
                f"[iter {current_iter}{limit_str}] "
                f"prompt={prompt_rec.id} "
                f"avg={avg_score:.1f} best_ever={best_ever:.1f} "
                f"cov={sub['coverage']:.0f} brd={sub['breadth']:.0f} "
                f"addr={sub['addressability']:.0f} eff={sub['efficiency']:.0f} "
                f"elapsed={_format_elapsed(total_elapsed)}"
            )

            # Generate next prompt
            score_history = db.get_recent_score_history(limit=10)
            plateau = _detect_plateau(score_history)

            try:
                learner_result = _call_learner_llm(
                    current_prompt=prompt_rec.prompt_text,
                    score_history=score_history,
                    sub_scores=sub,
                    plateau_detected=plateau,
                )
                new_prompt = learner_result.get("prompt", "")
                change_desc = learner_result.get("change_description", "")

                if new_prompt and new_prompt != prompt_rec.prompt_text:
                    db.insert_prompt(
                        prompt_text=new_prompt,
                        parent_id=prompt_rec.id,
                        generation=prompt_rec.generation + 1,
                        change_description=change_desc,
                    )
                    if change_desc:
                        print(f"  -> Change: {change_desc}")
                else:
                    print("  -> Learner returned unchanged prompt, retrying next iteration")
            except Exception as e:
                print(f"  -> Learner LLM error: {e}")

            # Periodic summary
            if current_iter % 10 == 0 and iteration_times:
                avg_time = sum(iteration_times) / len(iteration_times)
                print(f"  [summary] avg iter time: {_format_elapsed(avg_time)}, best ever: {best_ever:.1f}")

            time.sleep(cooldown)

    except KeyboardInterrupt:
        print(f"\n\nInterrupted at iteration {current_iter}.")

    # Auto-summary
    total_elapsed = time.monotonic() - start_time
    iterations_done = current_iter - completed

    print(f"\n{'='*60}")
    print(f"Learner finished: {iterations_done} iteration(s) in {_format_elapsed(total_elapsed)}")

    best = db.get_best_prompt()
    if best:
        best_avg = db.get_avg_score_for_prompt(best.id)
        print(f"Best prompt: id={best.id} (generation {best.generation}), avg score={best_avg:.1f}")

        lineage = db.get_prompt_lineage(best.id)
        if len(lineage) > 1:
            print(f"\nEvolution ({len(lineage)} steps):")
            for p in lineage:
                p_avg = db.get_avg_score_for_prompt(p.id)
                score_str = f"avg={p_avg:.1f}" if p_avg is not None else "not scored"
                desc = p.change_description or "seed"
                print(f"  gen {p.generation}: [{score_str}] {desc}")

    # Auto-generate report
    try:
        report_path = generate_report(db)
        print(f"\nReport: {report_path}")
    except Exception as e:
        print(f"\nReport generation failed: {e}")

    db.close()
