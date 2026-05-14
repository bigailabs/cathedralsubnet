"""bug_repro fixtures.

Each fixture is a self-contained buggy/fixed source pair with an issue
report and a hidden expected symptom (a substring the failing test's
output should contain). Curated for the alpha — 3 only.
"""

from __future__ import annotations

BUG_REPRO_FIXTURES: list[dict] = [
    {
        "fixture_id": "off_by_one_sum",
        "issue_title": "sum_range(1, 5) returns 14, expected 15",
        "issue_body": (
            "`sum_range(start, end)` is supposed to sum integers in [start, end]\n"
            "inclusive. It is off by one on the upper bound."
        ),
        "module_name": "buggy",
        "buggy_source": (
            "def sum_range(start, end):\n"
            "    total = 0\n"
            "    for i in range(start, end):  # off by one — should be end+1\n"
            "        total += i\n"
            "    return total\n"
        ),
        "fixed_source": (
            "def sum_range(start, end):\n"
            "    total = 0\n"
            "    for i in range(start, end + 1):\n"
            "        total += i\n"
            "    return total\n"
        ),
        "expected_symptom": "AssertionError",
        "expected_call_hint": "sum_range(1, 5) should equal 15",
        # Validator-side reference: the heuristic baseline miner reads
        # this directly. It is NEVER placed into job.context, so it
        # never reaches an LLM miner or any training prompt.
        "reference_test_source": (
            "from buggy import sum_range\nassert sum_range(1, 5) == 15, f'got {sum_range(1, 5)}'\n"
        ),
    },
    {
        "fixture_id": "wrong_default_arg",
        "issue_title": "append_item shares state across calls",
        "issue_body": (
            "`append_item(x, items=[])` is supposed to add `x` to a fresh list\n"
            "when no list is provided, but state leaks across calls."
        ),
        "module_name": "buggy",
        "buggy_source": (
            "def append_item(x, items=[]):  # mutable default arg bug\n"
            "    items.append(x)\n"
            "    return items\n"
        ),
        "fixed_source": (
            "def append_item(x, items=None):\n"
            "    if items is None:\n"
            "        items = []\n"
            "    items.append(x)\n"
            "    return items\n"
        ),
        "expected_symptom": "AssertionError",
        "expected_call_hint": "append_item('a'); append_item('b') second call should not see 'a'",
        "reference_test_source": (
            "from buggy import append_item\n"
            "first = append_item('a')\n"
            "second = append_item('b')\n"
            "assert second == ['b'], f'state leaked: got {second}'\n"
        ),
    },
    {
        "fixture_id": "divide_by_zero_guard",
        "issue_title": "safe_divide(1, 0) raises ZeroDivisionError instead of returning None",
        "issue_body": (
            "`safe_divide(a, b)` is documented to return None when b == 0.\nIt currently raises."
        ),
        "module_name": "buggy",
        "buggy_source": ("def safe_divide(a, b):\n    return a / b  # missing zero guard\n"),
        "fixed_source": (
            "def safe_divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n"
        ),
        "expected_symptom": "ZeroDivisionError",
        "expected_call_hint": "safe_divide(1, 0) should return None, not raise",
        "reference_test_source": (
            "from buggy import safe_divide\n"
            "assert safe_divide(1, 0) is None, f'got {safe_divide(1, 0)!r}'\n"
        ),
    },
]


__all__ = ["BUG_REPRO_FIXTURES"]
