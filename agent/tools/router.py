"""
router — Complexity Classification + LLM Tier Routing

Multi-tier routing pipeline:
  1. User prompt → complexity classifier (small/efficient model)
  2. Complexity score determines LLM tier + search depth
  3. Web search always first (SearXNG, low cost)
  4. HyDE generates hypothetical from web context
  5. KB search expands based on routing topic/intent
  6. Full answer assembled according to complexity tier

All weights and thresholds are exposed in ROUTING_CONFIG for tuning.
"""

import os
import re
import json
from dataclasses import dataclass, field

from .llm_client import llm_call
from config import get_settings

# ── Embedding model routing ───────────────────────────────────────────────────
SOURCE_MODEL_MAP = {
    "github": "bge-m3",
}
DEFAULT_MODEL = "mxbai-embed-large"

# ── Routing Configuration ─────────────────────────────────────────────────────
# Exposed weights/thresholds — modify as new pipelines or models are added.

ROUTING_CONFIG = {
    # Complexity thresholds (0.0–1.0)
    "thresholds": {
        "simple": 0.3,  # below this → simple answer, web only
        "moderate": 0.6,  # below this → web + HyDE + targeted KB
        # above this → full pipeline with expanded KB search
    },
    # LLM tier mapping — uses preferred model from each tier chain
    "llm_tiers": {
        "low": get_settings().resolve_model("fast"),
        "mid": get_settings().resolve_model("smart"),
        "high": get_settings().resolve_model("reasoning"),
    },
    # Complexity scoring weights (sum doesn't need to be 1.0 — raw score)
    "weights": {
        "length": 0.15,  # longer queries tend to be more complex
        "multi_question": 0.25,  # multiple questions = more complex
        "technical_terms": 0.20,  # domain-specific jargon
        "comparison": 0.15,  # "compare X vs Y" type queries
        "synthesis": 0.15,  # requests for summaries, analysis, deep dives
        "temporal": 0.10,  # time-bounded queries need more context
    },
    # Search depth per tier
    "search_depth": {
        "low": {
            "web_results": 3,
            "hyde": False,
            "kb_layers": 1,
            "kb_budget": 3,
            "query_expansion": 1,
        },
        "mid": {
            "web_results": 5,
            "hyde": True,
            "kb_layers": 3,
            "kb_budget": 5,
            "query_expansion": 2,
        },
        "high": {
            "web_results": 8,
            "hyde": True,
            "kb_layers": 5,
            "kb_budget": 8,
            "query_expansion": 4,
        },
    },
    # Intent → layer mapping (which KB sources to query per intent)
    "intent_layers": {
        "code": ["github", "wikipedia", "joplin_notes"],
        "research": ["arxiv", "biorxiv", "wikipedia", "news"],
        "current": ["news", "wikipedia"],
        "general": ["wikipedia", "github", "joplin_notes", "arxiv", "news"],
    },
    # Layer budgets (max results per layer)
    "layer_budgets": {
        "github": 6,
        "arxiv": 4,
        "biorxiv": 4,
        "wikipedia": 3,
        "news": 4,
        "joplin_notes": 2,
    },
}


# ── Intent Detection (lightweight, no LLM needed) ─────────────────────────────

_CODE_KEYWORDS = re.compile(
    r"\b("
    r"implement|implementation|code|coding|function|class|api|framework|"
    r"library|package|module|sdk|cli|server|client|middleware|router|"
    r"deploy|docker|kubernetes|container|microservice|rest|graphql|"
    r"python|javascript|typescript|rust|golang|java|react|vue|django|"
    r"fastapi|langchain|langgraph|how to build|how to create|"
    r"architecture|design pattern|boilerplate|template|scaffold|"
    r"open source|repo|repository|github|git|npm|pip|cargo"
    r")\b",
    re.IGNORECASE,
)

_RESEARCH_KEYWORDS = re.compile(
    r"\b("
    r"paper|study|research|model|algorithm|neural network|machine learning|"
    r"deep learning|transformer|llm|training|fine-tune|benchmark|"
    r"experiment|hypothesis|methodology|results|findings|arxiv|biorxiv"
    r")\b",
    re.IGNORECASE,
)

_TEMPORAL_KEYWORDS = re.compile(
    r"\b("
    r"latest|recent|new|current|today|this week|this month|this year|"
    r"breaking|just announced|released|updated|changed|announced|"
    r"2025|2026|2027"
    r")\b",
    re.IGNORECASE,
)


def detect_intent(topic: str) -> str:
    """Return intent: code, research, current, or general."""
    code_score = len(_CODE_KEYWORDS.findall(topic))
    research_score = len(_RESEARCH_KEYWORDS.findall(topic))
    temporal_score = len(_TEMPORAL_KEYWORDS.findall(topic))

    if code_score >= 2 or (
        code_score >= 1 and code_score > research_score and code_score > temporal_score
    ):
        return "code"
    if research_score >= 2:
        return "research"
    if temporal_score >= 1:
        return "current"
    return "general"


# ── Complexity Classification ─────────────────────────────────────────────────


@dataclass
class ComplexityResult:
    score: float  # 0.0–1.0
    tier: str  # "low", "mid", "high"
    intent: str  # "code", "research", "current", "general"
    reasoning: str  # brief explanation
    search_params: dict = field(default_factory=dict)


def _score_complexity_heuristic(query: str) -> float:
    """Fast heuristic scoring without LLM call."""
    w = ROUTING_CONFIG["weights"]
    score = 0.0

    # Length signal (normalize to 0–1, cap at 200 chars)
    score += w["length"] * min(len(query) / 200, 1.0)

    # Multiple questions
    question_count = query.count("?")
    score += w["multi_question"] * min(question_count / 3, 1.0)

    # Technical terms (words with mixed case, underscores, or camelCase)
    tech_terms = len(re.findall(r"\b[A-Z][a-z]+[A-Z]\w*|\b\w+_\w+\b", query))
    score += w["technical_terms"] * min(tech_terms / 5, 1.0)

    # Comparison signals
    comparison = bool(
        re.search(r"\b(versus|vs\.?|compare|difference|better|worse)\b", query, re.I)
    )
    score += w["comparison"] * (1.0 if comparison else 0.0)

    # Synthesis signals
    synthesis = bool(
        re.search(
            r"\b(explain|analyze|summarize|deep dive|comprehensive|thorough|detailed)\b",
            query,
            re.I,
        )
    )
    score += w["synthesis"] * (1.0 if synthesis else 0.0)

    # Temporal signals
    temporal = bool(_TEMPORAL_KEYWORDS.search(query))
    score += w["temporal"] * (1.0 if temporal else 0.0)

    return min(score, 1.0)


async def classify_complexity(query: str) -> ComplexityResult:
    """Classify query complexity using LLM (GPU when available) + heuristic fallback."""
    intent = detect_intent(query)

    # Try LLM classification first (routes to GPU for simple tier)
    try:
        llm_result = await _classify_with_llm(query)
        if llm_result is not None:
            return llm_result
    except Exception:
        pass

    # Fallback to heuristic
    score = _score_complexity_heuristic(query)
    tier = _score_to_tier(score)
    params = ROUTING_CONFIG["search_depth"][tier]

    return ComplexityResult(
        score=round(score, 2),
        tier=tier,
        intent=intent,
        reasoning=f"heuristic score {score:.2f}",
        search_params=params,
    )


async def _classify_with_llm(query: str) -> ComplexityResult | None:
    """Use a small model to classify query complexity — routed to GPU when available."""
    prompt = (
        f"Classify this query's complexity on a scale of 0.0 to 1.0:\n"
        f'"{query}"\n\n'
        f"Consider:\n"
        f"- Is it a simple factual question or a multi-part analysis?\n"
        f"- Does it require synthesis across multiple sources?\n"
        f"- Is it time-sensitive or requiring current context?\n\n"
        f"Return ONLY a JSON object with these fields:\n"
        f"  score: float (0.0-1.0)\n"
        f'  tier: "low" | "mid" | "high"\n'
        f"  reasoning: brief explanation (one sentence)\n\n"
        f"Guidelines:\n"
        f"- score < 0.3 → low (simple fact, definition, quick lookup)\n"
        f"- score 0.3-0.6 → mid (multi-part, needs context, some synthesis)\n"
        f"- score > 0.6 → high (deep analysis, comparison, comprehensive research)"
    )

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None

    try:
        result = await llm_call(
            messages=[{"role": "user", "content": prompt}],
            tier="simple",
            max_tokens=100,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        if result is None:
            return None

        if isinstance(result, dict):
            data = result
        elif isinstance(result, list):
            data = {"score": 0.5, "tier": "mid", "reasoning": "LLM returned list"}
        else:
            data = json.loads(result)

        score = float(data.get("score", 0.5))
        tier = data.get("tier", "mid")
        reasoning = data.get("reasoning", "")

        score = max(0.0, min(1.0, score))
        if tier not in ("low", "mid", "high"):
            tier = _score_to_tier(score)

        return ComplexityResult(
            score=round(score, 2),
            tier=tier,
            intent=detect_intent(query),
            reasoning=reasoning,
            search_params=ROUTING_CONFIG["search_depth"][tier],
        )
    except Exception:
        return None


def _score_to_tier(score: float) -> str:
    t = ROUTING_CONFIG["thresholds"]
    if score < t["simple"]:
        return "low"
    if score < t["moderate"]:
        return "mid"
    return "high"
