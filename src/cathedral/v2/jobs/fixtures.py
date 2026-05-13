"""Bundled fixtures so the loop runs without external corpora.

Real data goes here later. For now: minimal but realistic per task type.
"""

from __future__ import annotations


RESEARCH_CORPUS: list[dict] = [
    {
        "corpus_id": "eu-ai-act-2024",
        "question": "Under the EU AI Act, what is the maximum penalty for placing on the market an AI system that violates prohibited-practice rules (Article 5)?",
        "passages": [
            {"id": "p1", "text": "Article 5 enumerates prohibited AI practices including manipulative subliminal techniques and untargeted scraping of facial images."},
            {"id": "p2", "text": "Article 99 sets administrative fines. For non-compliance with Article 5 the upper limit is EUR 35,000,000 or 7% of worldwide annual turnover, whichever is higher."},
            {"id": "p3", "text": "For other infringements the maximum is EUR 15,000,000 or 3% of worldwide annual turnover."},
        ],
        "answer": "EUR 35,000,000 or 7% of worldwide annual turnover, whichever is higher.",
        "required_citations": ["p2"],
    },
    {
        "corpus_id": "us-ai-eo-14110",
        "question": "Which US agency was directed by EO 14110 to establish reporting requirements for dual-use foundation models with potential to enable mass-casualty CBRN harm?",
        "passages": [
            {"id": "p1", "text": "Executive Order 14110 of October 30, 2023 directs the Secretary of Commerce."},
            {"id": "p2", "text": "Sec. 4.2 requires the Secretary of Commerce, acting through BIS, to require reporting from developers of dual-use foundation models."},
            {"id": "p3", "text": "Sec. 4.6 covers open weights and is directed at NTIA."},
        ],
        "answer": "The Department of Commerce, acting through the Bureau of Industry and Security (BIS).",
        "required_citations": ["p2"],
    },
    {
        "corpus_id": "uk-ai-whitepaper-2023",
        "question": "What is the central regulatory philosophy of the UK AI Whitepaper?",
        "passages": [
            {"id": "p1", "text": "The UK AI Whitepaper proposes a pro-innovation approach: principles applied by existing regulators rather than a single AI act."},
            {"id": "p2", "text": "Five cross-sectoral principles are issued for regulators: safety, transparency, fairness, accountability, contestability."},
        ],
        "answer": "Pro-innovation: existing sector regulators apply five cross-sectoral principles, no single new AI act.",
        "required_citations": ["p1"],
    },
]


CODE_PATCH_FIXTURES: list[dict] = [
    {
        "task": "Fix the `add` function so that the failing test passes. The function currently subtracts instead of adding.",
        "filename": "math_utils.py",
        "source": "def add(a, b):\n    return a - b\n",
        "failing_test": "from math_utils import add\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\n",
        "expected_patch": "--- a/math_utils.py\n+++ b/math_utils.py\n@@\n-def add(a, b):\n-    return a - b\n+def add(a, b):\n+    return a + b\n",
    },
    {
        "task": "Make `is_even` return True for even ints and False otherwise. The current implementation is inverted.",
        "filename": "parity.py",
        "source": "def is_even(n):\n    return n % 2 == 1\n",
        "failing_test": "from parity import is_even\nassert is_even(2) is True\nassert is_even(3) is False\nassert is_even(0) is True\n",
        "expected_patch": "--- a/parity.py\n+++ b/parity.py\n@@\n-def is_even(n):\n-    return n % 2 == 1\n+def is_even(n):\n+    return n % 2 == 0\n",
    },
    {
        "task": "Fix `clamp(value, lo, hi)` to return value clamped to [lo, hi]. Currently it ignores lo.",
        "filename": "clamp.py",
        "source": "def clamp(value, lo, hi):\n    return min(value, hi)\n",
        "failing_test": "from clamp import clamp\nassert clamp(5, 0, 10) == 5\nassert clamp(-3, 0, 10) == 0\nassert clamp(15, 0, 10) == 10\n",
        "expected_patch": "--- a/clamp.py\n+++ b/clamp.py\n@@\n-def clamp(value, lo, hi):\n-    return min(value, hi)\n+def clamp(value, lo, hi):\n+    return max(lo, min(value, hi))\n",
    },
]


TOOL_ROUTE_FIXTURES: list[dict] = [
    {
        "goal": "I need to know the current weather in Paris.",
        "expected_tool": "weather_lookup",
        "expected_args": {"city": "Paris"},
        "available_tools": [
            {"name": "weather_lookup", "description": "Look up current weather for a city."},
            {"name": "send_email", "description": "Send an email to a recipient."},
            {"name": "calc", "description": "Evaluate an arithmetic expression."},
        ],
    },
    {
        "goal": "Compute 17 * 23 + 4.",
        "expected_tool": "calc",
        "expected_args": {"expression": "17 * 23 + 4"},
        "available_tools": [
            {"name": "weather_lookup", "description": "Look up weather."},
            {"name": "calc", "description": "Evaluate an arithmetic expression."},
            {"name": "send_email", "description": "Send an email."},
        ],
    },
    {
        "goal": "Send a quick note to alice@example.com saying the standup is moved to 11am.",
        "expected_tool": "send_email",
        "expected_args": {"to": "alice@example.com", "subject": "standup", "body": "moved to 11am"},
        "available_tools": [
            {"name": "weather_lookup", "description": "Weather."},
            {"name": "send_email", "description": "Send an email."},
            {"name": "calc", "description": "Math."},
        ],
    },
]


MULTI_STEP_WORLDS: list[dict] = [
    {
        "goal": "Find the password for user 'alice' in the KV store under 'creds/' and set 'session/alice' to that value, then call done.",
        "initial_state": {
            "creds/alice": "wonderland42",
            "creds/bob": "builder99",
            "session/bob": "",
        },
        "target_state": {
            "session/alice": "wonderland42",
        },
        "min_steps": 3,
        "max_steps": 8,
    },
    {
        "goal": "Search for 'cathedral verifier' and store the first result's url in kv key 'lookup/result', then call done.",
        "initial_state": {},
        "target_state": {
            "lookup/result": "https://cathedral.computer/docs/verifier",
        },
        "min_steps": 2,
        "max_steps": 6,
    },
]


CLASSIFY_FIXTURES: list[dict] = [
    {
        "task_description": "Classify the user message into one of the labels.",
        "text": "When I click the submit button on the form, the page just hangs forever and I never get a response.",
        "labels": ["bug", "feature_request", "praise", "question"],
        "expected_label": "bug",
    },
    {
        "task_description": "Classify the user message into one of the labels.",
        "text": "It would be amazing if we could export the dashboard as PDF.",
        "labels": ["bug", "feature_request", "praise", "question"],
        "expected_label": "feature_request",
    },
    {
        "task_description": "Classify the user message into one of the labels.",
        "text": "Honestly the new release is the smoothest deploy we've ever had, great work everyone.",
        "labels": ["bug", "feature_request", "praise", "question"],
        "expected_label": "praise",
    },
    {
        "task_description": "Classify the user message into one of the labels.",
        "text": "How do I change my notification settings?",
        "labels": ["bug", "feature_request", "praise", "question"],
        "expected_label": "question",
    },
]


__all__ = [
    "CLASSIFY_FIXTURES",
    "CODE_PATCH_FIXTURES",
    "MULTI_STEP_WORLDS",
    "RESEARCH_CORPUS",
    "TOOL_ROUTE_FIXTURES",
]
