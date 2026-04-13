"""
Build golden query fixtures by resolving entity names via the Elemental API.

Run from the agents/ directory:
    python -m research_learner.build_fixtures
    python -m research_learner.build_fixtures --interactive
    python -m research_learner.build_fixtures --interactive --save-overrides

Requires ELEMENTAL_API_URL + ELEMENTAL_API_TOKEN env vars (or broadchurch.yaml).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from broadchurch_auth import elemental_client

_OVERRIDES_PATH = Path(__file__).parent / "entity_overrides.json"

# ---------------------------------------------------------------------------
# Thesis definitions — edit this list to change the golden query set
# ---------------------------------------------------------------------------

THESIS_DEFS = [
    # --- 1-entity prompts ---
    {
        "thesis": "NVIDIA's revenue growth is primarily driven by its data center segment rather than gaming",
        "entities": ["NVIDIA"],
        "claims": [
            "Data center revenue has grown faster than gaming in recent quarterly filings",
            "News coverage of NVIDIA focuses on AI infrastructure rather than gaming",
        ],
        "data_needs": ["filings", "stock_prices", "news"],
    },
    {
        "thesis": "Apple insiders have been net sellers of company stock over the past year",
        "entities": ["Apple"],
        "claims": [
            "Recent Form 4 filings show more insider sell transactions than buys",
            "Executive officers have reduced their equity holdings",
        ],
        "data_needs": ["filings", "relationships", "news"],
    },
    {
        "thesis": "Johnson and Johnson's corporate restructuring has improved its financial profile",
        "entities": ["Johnson & Johnson"],
        "claims": [
            "8-K filings document significant organizational changes",
            "Recent 10-Q filings show margin improvement in the company's continuing operations",
        ],
        "data_needs": ["filings", "events", "news"],
    },
    # --- 2-entity prompts ---
    {
        "thesis": "JPMorgan Chase is growing its net interest income faster than Wells Fargo",
        "entities": ["JPMorgan Chase", "Wells Fargo"],
        "claims": [
            "JPMorgan's quarterly filings show higher net interest income growth",
            "Wells Fargo's lending growth has been constrained relative to JPMorgan",
        ],
        "data_needs": ["filings", "stock_prices", "news"],
    },
    {
        "thesis": "Microsoft's cloud business is growing faster than Alphabet's cloud division",
        "entities": ["Microsoft", "Alphabet"],
        "claims": [
            "Microsoft's cloud segment revenue growth exceeds Google Cloud revenue growth in recent 10-K filings",
            "Both companies identify cloud as a primary growth driver in their annual filings",
        ],
        "data_needs": ["filings", "stock_prices", "news"],
    },
    {
        "thesis": "PepsiCo's diversified portfolio gives it more stable revenue than Coca-Cola",
        "entities": ["PepsiCo", "Coca-Cola"],
        "claims": [
            "PepsiCo's total revenue exceeds Coca-Cola's due to its snack and food segments",
            "Coca-Cola's revenue is more concentrated in beverages per 10-K segment disclosures",
        ],
        "data_needs": ["filings", "stock_prices", "news"],
    },
    {
        "thesis": "ExxonMobil's acquisition activity positions it ahead of Chevron for production growth",
        "entities": ["ExxonMobil", "Chevron"],
        "claims": [
            "ExxonMobil's 8-K filings show more recent acquisition activity than Chevron's",
            "Chevron's quarterly production volume growth trails ExxonMobil's based on recent filings",
        ],
        "data_needs": ["filings", "events", "news", "relationships"],
    },
    # --- 3-entity prompts ---
    {
        "thesis": "Apple, Microsoft, and Alphabet hold the largest cash reserves among US tech companies",
        "entities": ["Apple", "Microsoft", "Alphabet"],
        "claims": [
            "All three report cash and short-term investments exceeding $50 billion in recent quarterly filings",
            "Their combined cash positions have grown year-over-year",
        ],
        "data_needs": ["filings", "stock_prices", "news"],
    },
    {
        "thesis": "Visa and Mastercard's asset-light network model produces higher margins than American Express",
        "entities": ["Visa", "Mastercard", "American Express"],
        "claims": [
            "Visa and Mastercard report higher operating margins than American Express in 10-K filings",
            "American Express carries credit risk on its balance sheet that Visa and Mastercard do not",
        ],
        "data_needs": ["filings", "stock_prices", "news", "relationships"],
    },
    {
        "thesis": "Amazon and Walmart are outpacing Target in the retail competition",
        "entities": ["Amazon", "Walmart", "Target"],
        "claims": [
            "Amazon and Walmart's revenue growth in recent quarterly filings exceeds Target's",
            "Target's stock performance has lagged both Amazon and Walmart",
        ],
        "data_needs": ["filings", "stock_prices", "news", "events"],
    },
]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return slug.strip("_")[:60]


def _load_overrides() -> dict[str, dict]:
    """Load entity_overrides.json if it exists."""
    if _OVERRIDES_PATH.exists():
        try:
            return json.loads(_OVERRIDES_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not load {_OVERRIDES_PATH}: {e}")
    return {}


def _save_overrides(overrides: dict[str, dict]) -> None:
    """Write entity_overrides.json."""
    _OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2) + "\n")
    print(f"\nSaved overrides to {_OVERRIDES_PATH}")


def _fetch_candidates(name: str) -> list[dict]:
    """Fetch up to 10 entity search candidates for a name."""
    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": name}],
                "maxResults": 10,
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.3,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results or not results[0].get("matches"):
            return []
        return results[0]["matches"]
    except Exception as e:
        print(f"  ERROR fetching candidates for '{name}': {e}")
        return []


def _pick_best_org(matches: list[dict], min_score: float = 0.5) -> int | None:
    """Find the index of the best organization match with score >= min_score."""
    for i, m in enumerate(matches):
        if m.get("flavor") == "organization" and m.get("score", 0) >= min_score:
            return i
    return None


def _resolve_entity_auto(name: str) -> dict | None:
    """Auto-resolve: prefer organization among high-scoring matches."""
    matches = _fetch_candidates(name)
    if not matches:
        return None

    org_idx = _pick_best_org(matches)
    if org_idx is not None:
        m = matches[org_idx]
        return {
            "name": m.get("name", name),
            "neid": m["neid"],
            "type": m.get("flavor"),
            "score": m.get("score"),
        }

    m = matches[0]
    return {
        "name": m.get("name", name),
        "neid": m["neid"],
        "type": m.get("flavor"),
        "score": m.get("score"),
    }


def _resolve_entity_interactive(name: str) -> dict | None:
    """Interactive: show all candidates, suggest best org, let user choose."""
    matches = _fetch_candidates(name)
    if not matches:
        print("    No matches found.")
        return None

    org_idx = _pick_best_org(matches)
    suggestion = org_idx if org_idx is not None else 0

    for i, m in enumerate(matches):
        marker = " *" if i == suggestion else "  "
        print(
            f"   {marker}[{i + 1}]  {m.get('name', '?'):<40s} "
            f"{m.get('flavor', '?'):<25s} "
            f"score={m.get('score', 0):.2f}  {m['neid']}"
        )

    reason = "(preferred organization)" if org_idx is not None else "(top match)"
    print(f"    Auto-pick: [{suggestion + 1}] {reason}")

    while True:
        choice = input(f"    Choice [{suggestion + 1}]: ").strip()
        if not choice:
            idx = suggestion
            break
        if choice.lower() == "s":
            print("    Skipped.")
            return None
        if choice.lower().startswith("neid="):
            manual_neid = choice[5:].strip()
            return {
                "name": name,
                "neid": manual_neid,
                "type": "manual",
                "score": None,
            }
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                break
            print(f"    Invalid choice. Enter 1-{len(matches)}, 's' to skip, or 'neid=XXXX'.")
        except ValueError:
            print(f"    Invalid input. Enter 1-{len(matches)}, 's' to skip, or 'neid=XXXX'.")

    m = matches[idx]
    return {
        "name": m.get("name", name),
        "neid": m["neid"],
        "type": m.get("flavor"),
        "score": m.get("score"),
    }


def build_queries(interactive: bool = False) -> tuple[dict[str, dict], dict[str, dict]]:
    """Resolve all thesis entities and build the QUERIES dict.

    Returns (queries, resolutions) where resolutions maps entity names
    to the chosen resolution dict, for optional override saving.
    """
    overrides = _load_overrides()
    if overrides:
        print(f"Loaded {len(overrides)} entity override(s) from {_OVERRIDES_PATH}")

    queries: dict[str, dict] = {}
    resolutions: dict[str, dict] = {}

    for td in THESIS_DEFS:
        thesis = td["thesis"]
        slug = _slugify(thesis)
        print(f"\n--- {slug} ---")
        print(f"  Thesis: {thesis}")

        entities = []
        all_resolved = True
        for entity_name in td["entities"]:
            print(f"  Resolving '{entity_name}'...")

            if entity_name in overrides:
                ov = overrides[entity_name]
                print(
                    f"    [override] -> {ov['name']} "
                    f"(NEID: {ov['neid']}, type: {ov.get('type', '?')})"
                )
                result = ov
                resolutions[entity_name] = ov
            elif interactive:
                result = _resolve_entity_interactive(entity_name)
                if result:
                    resolutions[entity_name] = result
            else:
                result = _resolve_entity_auto(entity_name)
                if result:
                    resolutions[entity_name] = result

            if result:
                if entity_name not in overrides:
                    print(
                        f"    -> {result['name']} "
                        f"(NEID: {result['neid']}, type: {result.get('type', '?')}, "
                        f"score: {result.get('score', '?')})"
                    )
                entities.append({
                    "mentioned_as": entity_name,
                    "status": "resolved",
                    "name": result["name"],
                    "neid": result["neid"],
                    "type": result.get("type"),
                })
            else:
                print("    FAILED")
                all_resolved = False

        if not all_resolved:
            print(f"  SKIPPING {slug} — not all entities resolved")
            continue

        queries[slug] = {
            "thesis_plaintext": thesis,
            "entities": entities,
            "claims": td["claims"],
            "data_needs": td["data_needs"],
        }
        print(f"  OK ({len(entities)} entities)")

    return queries, resolutions


def write_fixtures(queries: dict[str, dict]) -> None:
    """Write the QUERIES dict to fixtures.py."""
    out_path = Path(__file__).parent / "fixtures.py"
    lines = [
        '"""Golden query fixtures — generated by build_fixtures.py. Do not edit manually."""',
        "",
        "QUERIES = " + json.dumps(queries, indent=4, default=str),
        "",
    ]
    out_path.write_text("\n".join(lines))
    print(f"\nWrote {len(queries)} queries to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build golden query fixtures")
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Show all candidates and prompt for each entity",
    )
    parser.add_argument(
        "--save-overrides", action="store_true",
        help="Save interactive choices to entity_overrides.json",
    )
    args = parser.parse_args()

    print("Building golden query fixtures...")
    print("Using Elemental API at:", elemental_client.base_url)

    queries, resolutions = build_queries(interactive=args.interactive)
    if not queries:
        print("\nERROR: No queries resolved. Check your API credentials.")
        sys.exit(1)

    write_fixtures(queries)

    if args.save_overrides and resolutions:
        existing = _load_overrides()
        existing.update(resolutions)
        _save_overrides(existing)
    elif args.interactive and resolutions:
        print("\nTip: re-run with --save-overrides to persist these choices")

    print("Done.")


if __name__ == "__main__":
    main()
