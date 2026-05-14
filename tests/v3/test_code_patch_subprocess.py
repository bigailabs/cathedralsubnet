"""Hardening tests for the fixture-only code_patch subprocess path.

These tests cover guardrails on _parse_module_name and _run_python_test. The
runner is NOT a sandbox — it's only safe because both `source` and `test`
come from the validator-owned fixture set in cathedral.v3.jobs.fixtures.
These tests pin that contract.
"""

from __future__ import annotations

import pytest

from cathedral.v3.validator.tools import _parse_module_name, _run_python_test

# ---------------------------------------------------------------------------
# _parse_module_name guardrails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "test_source,expected",
    [
        ("import foo\nfoo.bar()", "foo"),
        ("from foo import bar\nbar()", "foo"),
        ("# comment\n\nfrom my_mod import f\n", "my_mod"),
        ("from foo import a, b\n", "foo"),
        ("import foo, bar\n", "foo"),
    ],
)
def test_parse_module_name_happy_path(test_source: str, expected: str) -> None:
    assert _parse_module_name(test_source) == expected


@pytest.mark.parametrize(
    "test_source",
    [
        "",  # empty
        "x = 1\n",  # no import
        "from a.b.c import d\n",  # dotted package — refused
        "from .relative import x\n",  # relative — refused
        "import os\n",  # stdlib — refused (would shadow os)
        "import sys\n",  # stdlib — refused
        "import subprocess\n",  # stdlib — refused
        "from ../etc/passwd import secrets\n",  # path-like — refused
        "import 1bad\n",  # invalid identifier
        "from __init__ import x\n",  # leading underscore family — handled
    ],
)
def test_parse_module_name_falls_back_on_malformed(test_source: str) -> None:
    # All malformed / unsafe imports must fall back to the fixed default.
    assert _parse_module_name(test_source) == "module_under_test"


def test_parse_module_name_rejects_path_separators() -> None:
    # Defense in depth — even if the regex changes, ensure no path chars
    # ever survive into a filename.
    for bad in ["from a/b import c\n", "from ../x import y\n", "import a\\b\n"]:
        out = _parse_module_name(bad)
        assert "/" not in out and "\\" not in out and ".." not in out


# ---------------------------------------------------------------------------
# _run_python_test end-to-end (still uses subprocess, kept tight)
# ---------------------------------------------------------------------------


def test_run_python_test_pass() -> None:
    source = "def add(a, b):\n    return a + b\n"
    test = "from solution import add\nassert add(2, 3) == 5\n"
    ok, err = _run_python_test(source, test)
    assert ok is True
    assert err is None


def test_run_python_test_fail_returns_error_tail() -> None:
    source = "def add(a, b):\n    return a - b\n"  # buggy
    test = "from solution import add\nassert add(2, 3) == 5\n"
    ok, err = _run_python_test(source, test)
    assert ok is False
    assert err is not None
    assert "AssertionError" in err or "non-zero" in err


def test_run_python_test_timeout() -> None:
    # patch the module-level timeout to 1s so the test runs fast.
    import cathedral.v3.validator.tools as tools

    original = tools._FIXTURE_TIMEOUT_SECONDS
    tools._FIXTURE_TIMEOUT_SECONDS = 1
    try:
        source = "import time\ndef sleeper():\n    time.sleep(10)\n"
        test = "from solution import sleeper\nsleeper()\n"
        ok, err = _run_python_test(source, test)
        assert ok is False
        assert err == "timeout"
    finally:
        tools._FIXTURE_TIMEOUT_SECONDS = original


def test_run_python_test_handles_malformed_test_source() -> None:
    # Empty test still runs (no imports => module_under_test) and the
    # assertion is whether returncode reflects the exec.
    source = "def f():\n    return 1\n"
    test = "raise SystemExit(2)\n"
    ok, err = _run_python_test(source, test)
    assert ok is False
    assert err is not None


def test_run_python_test_handles_test_with_no_imports() -> None:
    # Falls back to module_under_test; we just write `source` to that
    # filename — the test, if it doesn't import it, runs against nothing.
    source = "def f():\n    return 1\n"
    test = "assert 1 == 1\n"
    ok, _err = _run_python_test(source, test)
    assert ok is True
