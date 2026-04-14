"""
Research scorer — evaluates research output quality via Gemini with a fixed rubric.

Each dimension is scored 0-25, then the weighted total (0-100) is computed:
  - Coverage     (weight 0.20): Did research cover all entities in the query?
  - Breadth      (weight 0.20): Were all relevant data types fetched per data_needs?
  - Addressability (weight 0.50): Does the data help evaluate each claim?
  - Efficiency   (weight 0.10): Was research completed without wasted calls?
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_learner.log import get_logger

log = get_logger("scorer")

SCORER_INSTRUCTION = """\
You are a research quality evaluator. You receive a thesis query and the research
output produced by a financial research planner. Your job is to score the research
quality on four dimensions.

## Input

You receive JSON with:
- `query`: the thesis, entities (with NEIDs), claims, and data_needs
- `research`: the research document with a list of API calls made and their results

## Scoring rubric

Score each dimension from 0 to 25:

### Coverage (0-25)
Did the research cover ALL entities listed in query.entities?
- 25: Every entity had at least one successful data fetch
- 15-24: Most entities covered, minor gaps
- 5-14: Significant entities missed
- 0-4: Most entities not covered

### Breadth (0-25)
Were all relevant data types from query.data_needs fetched?
For each data_need (news, stock_prices, filings, events, relationships):
- Was it requested for at least one relevant entity?
- Did it return useful data (not just errors)?
- 25: All data_needs addressed with successful results
- 15-24: Most data_needs covered
- 5-14: Only 1-2 data types fetched
- 0-4: Almost no data variety

### Addressability (0-25)
Does the gathered data actually help evaluate each claim in query.claims?
- 25: Every claim can be assessed using the gathered data
- 15-24: Most claims addressable, minor gaps
- 5-14: Data is tangential to the claims
- 0-4: Data does not address the claims at all

### Efficiency (0-25)
Was the research completed without waste?
- Count duplicate calls (same type + same entity)
- Count calls that returned errors
- Count unnecessary iterations (e.g. retrying failed entities)
- 25: No duplicates, no wasted retries, efficient iteration count
- 15-24: Minor inefficiencies
- 5-14: Significant waste (many duplicates or error retries)
- 0-4: Mostly wasted calls

## Dimension weights

The total score is computed as a weighted sum of the four dimensions:
- Coverage: 20%
- Breadth: 20%
- Addressability: 50% (most important — research must help evaluate the claims)
- Efficiency: 10%

Addressability is the dominant factor. Focus your evaluation there most carefully.

## Output

Return ONLY a JSON object (no markdown, no explanation).
Score each dimension 0-25 independently. The weighted total is computed externally.

{
    "coverage": <0-25>,
    "breadth": <0-25>,
    "addressability": <0-25>,
    "efficiency": <0-25>,
    "reasoning": "<2-3 sentences explaining the scores>"
}
"""

DIMENSION_WEIGHTS = {
    "coverage": 0.20,
    "breadth": 0.20,
    "addressability": 0.50,
    "efficiency": 0.10,
}

MAX_RETRIES = 3
BACKOFF_SECONDS = [5, 15, 45]
LLM_CALL_TIMEOUT = 120


@dataclass
class ScoreResult:
    score: int
    coverage: int
    breadth: int
    addressability: int
    efficiency: int
    reasoning: str


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


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def score_research(query: dict, research_doc: dict) -> ScoreResult:
    """Score a research output against the query using the fixed rubric.

    Args:
        query: The QueryRewrite JSON (thesis, entities, claims, data_needs).
        research_doc: The research document with calls and results.

    Returns:
        ScoreResult with total and per-dimension scores.
    """
    from google import genai
    from google.genai import types

    scorer_input = json.dumps(
        {"query": query, "research": research_doc},
        default=str,
    )
    if len(scorer_input) > 200_000:
        scorer_input = scorer_input[:200_000] + '..."}'

    SCORER_MODEL = "gemini-2.5-flash-lite"
    log.info(f"Scorer LLM call starting (model={SCORER_MODEL}, input {len(scorer_input):,} chars)")

    t_client = time.monotonic()
    project, region = _load_gcp_config()
    client = genai.Client(vertexai=True, project=project, location=region)
    client_init_s = time.monotonic() - t_client

    def _generate() -> any:
        return client.models.generate_content(
            model=SCORER_MODEL,
            contents=scorer_input,
            config=types.GenerateContentConfig(
                system_instruction=SCORER_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

    t_retries = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            t_gen = time.monotonic()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_generate)
                response = future.result(timeout=LLM_CALL_TIMEOUT)
            result = json.loads(response.text)
            generate_s = time.monotonic() - t_gen

            coverage = _clamp(int(result.get("coverage", 0)), 0, 25)
            breadth = _clamp(int(result.get("breadth", 0)), 0, 25)
            addressability = _clamp(int(result.get("addressability", 0)), 0, 25)
            efficiency = _clamp(int(result.get("efficiency", 0)), 0, 25)
            total = round(
                (coverage / 25 * DIMENSION_WEIGHTS["coverage"]
                 + breadth / 25 * DIMENSION_WEIGHTS["breadth"]
                 + addressability / 25 * DIMENSION_WEIGHTS["addressability"]
                 + efficiency / 25 * DIMENSION_WEIGHTS["efficiency"])
                * 100
            )

            log.info(
                f"Scorer LLM returned: total={total} "
                f"cov={coverage} brd={breadth} addr={addressability} eff={efficiency} "
                f"(model={SCORER_MODEL}, client={client_init_s:.2f}s gen={generate_s:.1f}s)"
            )
            log.debug(f"Scorer LLM reasoning: {result.get('reasoning', '')}")

            return ScoreResult(
                score=total,
                coverage=coverage,
                breadth=breadth,
                addressability=addressability,
                efficiency=efficiency,
                reasoning=str(result.get("reasoning", "")),
            )
        except TimeoutError:
            generate_s = time.monotonic() - t_gen
            log.error(
                f"Scorer LLM call timed out after {generate_s:.0f}s "
                f"(attempt {attempt+1}/{MAX_RETRIES})"
            )
            last_error = TimeoutError(
                f"generate_content hung for {generate_s:.0f}s"
            )
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning(f"Scorer LLM rate limited (attempt {attempt+1}), backing off {wait}s: {e}")
                time.sleep(wait)
            else:
                log.error(f"Scorer LLM call failed: {e}")
                break

    elapsed = time.monotonic() - t_retries
    log.error(f"Scorer LLM exhausted retries (model={SCORER_MODEL}, client={client_init_s:.2f}s retries={elapsed:.1f}s): {last_error}")
    return ScoreResult(
        score=0,
        coverage=0,
        breadth=0,
        addressability=0,
        efficiency=0,
        reasoning=f"Scoring failed: {last_error}",
    )
