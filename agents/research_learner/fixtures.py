"""Golden query fixtures — placeholder until build_fixtures.py is run with API credentials.

Run `python -m research_learner.build_fixtures` with ELEMENTAL_API_TOKEN set to
populate this file with real NEIDs from the Elemental API.
"""

QUERIES = {
    "netflix_is_losing_market_share_to_disney_in_the_streaming_wa": {
        "thesis_plaintext": "Netflix is losing market share to Disney+ in the streaming wars",
        "entities": [
            {
                "mentioned_as": "Netflix",
                "status": "resolved",
                "name": "Netflix, Inc.",
                "neid": "PLACEHOLDER_NEID_NETFLIX",
                "type": "organization",
            },
            {
                "mentioned_as": "The Walt Disney Company",
                "status": "resolved",
                "name": "The Walt Disney Company",
                "neid": "PLACEHOLDER_NEID_DISNEY",
                "type": "organization",
            },
        ],
        "claims": [
            "Netflix subscriber growth is slowing",
            "Disney+ is gaining market share",
        ],
        "data_needs": ["news", "stock_prices", "filings", "events"],
    },
    "rising_interest_rates_are_hurting_commercial_real_estate_val": {
        "thesis_plaintext": "Rising interest rates are hurting commercial real estate valuations",
        "entities": [
            {
                "mentioned_as": "Simon Property Group",
                "status": "resolved",
                "name": "Simon Property Group, Inc.",
                "neid": "PLACEHOLDER_NEID_SPG",
                "type": "organization",
            },
            {
                "mentioned_as": "Prologis",
                "status": "resolved",
                "name": "Prologis, Inc.",
                "neid": "PLACEHOLDER_NEID_PLD",
                "type": "organization",
            },
        ],
        "claims": [
            "REIT valuations have declined as rates rose",
            "Higher borrowing costs are squeezing margins",
        ],
        "data_needs": ["stock_prices", "filings", "events", "news"],
    },
    "jpmorgan_is_better_positioned_than_goldman_sachs_for_a_reces": {
        "thesis_plaintext": "JPMorgan is better positioned than Goldman Sachs for a recession",
        "entities": [
            {
                "mentioned_as": "JPMorgan Chase",
                "status": "resolved",
                "name": "JPMorgan Chase & Co.",
                "neid": "PLACEHOLDER_NEID_JPM",
                "type": "organization",
            },
            {
                "mentioned_as": "Goldman Sachs",
                "status": "resolved",
                "name": "The Goldman Sachs Group, Inc.",
                "neid": "PLACEHOLDER_NEID_GS",
                "type": "organization",
            },
        ],
        "claims": [
            "JPMorgan has a more diversified revenue base",
            "Goldman Sachs is more exposed to trading revenue declines",
        ],
        "data_needs": ["stock_prices", "filings", "news", "events", "relationships"],
    },
    "apple_s_services_revenue_growth_is_offsetting_declining_ipho": {
        "thesis_plaintext": "Apple's services revenue growth is offsetting declining iPhone sales",
        "entities": [
            {
                "mentioned_as": "Apple",
                "status": "resolved",
                "name": "Apple Inc.",
                "neid": "PLACEHOLDER_NEID_AAPL",
                "type": "organization",
            },
        ],
        "claims": [
            "Services revenue as a share of total revenue is increasing",
            "iPhone unit sales have plateaued or declined",
        ],
        "data_needs": ["stock_prices", "filings", "news"],
    },
}
