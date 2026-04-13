"""
Build golden query fixtures by resolving entity names via the Elemental API.

Run from the agents/ directory:
    python -m research_learner.build_fixtures

Requires ELEMENTAL_API_URL + ELEMENTAL_API_TOKEN env vars (or broadchurch.yaml).
"""

import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from broadchurch_auth import elemental_client

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


def _resolve_entity(name: str) -> dict | None:
    """Search for an entity by name and return the top match."""
    try:
        resp = elemental_client.post(
            "/entities/search",
            json={
                "queries": [{"queryId": 1, "query": name}],
                "maxResults": 3,
                "includeNames": True,
                "includeFlavors": True,
                "includeScores": True,
                "minScore": 0.3,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results or not results[0].get("matches"):
            return None
        match = results[0]["matches"][0]
        return {
            "name": match.get("name", name),
            "neid": match["neid"],
            "type": match.get("flavor"),
            "score": match.get("score"),
        }
    except Exception as e:
        print(f"  ERROR resolving '{name}': {e}")
        return None


def build_queries() -> dict[str, dict]:
    """Resolve all thesis entities and build the QUERIES dict."""
    queries: dict[str, dict] = {}

    for td in THESIS_DEFS:
        thesis = td["thesis"]
        slug = _slugify(thesis)
        print(f"\n--- {slug} ---")
        print(f"  Thesis: {thesis}")

        entities = []
        all_resolved = True
        for entity_name in td["entities"]:
            print(f"  Resolving '{entity_name}'...", end=" ")
            result = _resolve_entity(entity_name)
            if result:
                print(f"-> {result['name']} (NEID: {result['neid']}, score: {result.get('score', '?')})")
                entities.append({
                    "mentioned_as": entity_name,
                    "status": "resolved",
                    "name": result["name"],
                    "neid": result["neid"],
                    "type": result.get("type"),
                })
            else:
                print("FAILED")
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

    return queries


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
    print("Building golden query fixtures...")
    print("Using Elemental API at:", elemental_client.base_url)
    queries = build_queries()
    if not queries:
        print("\nERROR: No queries resolved. Check your API credentials.")
        sys.exit(1)
    write_fixtures(queries)
    print("Done.")


if __name__ == "__main__":
    main()
