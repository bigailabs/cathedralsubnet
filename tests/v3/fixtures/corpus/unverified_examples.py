"""Unverified bug_isolation_v1 example rows.

**NON-PRODUCTION. PARSER + SCORER UNIT FIXTURES ONLY.**

Every row below has plausible structure, references a real Python
repo, and links to a real upstream advisory or issue. **The commit
SHAs and exact line ranges have not been verified against upstream
git history**, so none of them belong in production
``seed_pilot.py``.

These rows exist to exercise the schema, scorer, sampler, and
dispatch paths in unit tests without touching the production pilot
corpus.

These rows MUST NOT be imported by any module under
``src/cathedral/`` and MUST NOT be merged into
``src/cathedral/v3/corpus/seed_pilot.py`` without first:

1. Opening the ``source_url``.
2. Confirming the parent-of-fix commit SHA exists in upstream
   history (click through to the commit page).
3. Reading the diff and pinning the actual file path, line range,
   and failure_mode keywords from the fix body.
4. Updating the row here, then moving it into ``seed_pilot.py``
   and dropping the ``UNVERIFIED_`` prefix.
"""

from __future__ import annotations

from cathedral.v3.corpus.schema import ChallengeRow

UNVERIFIED_EXAMPLES: tuple[ChallengeRow, ...] = (
    # requests: Proxy-Authorization leak across redirect.
    # Advisory URL is real (GHSA-9wx4-h78v-vm56 / CVE-2024-35195),
    # but the commit SHA below is not verified against upstream as
    # the parent-of-fix.
    ChallengeRow(
        id="UNVERIFIED_requests_proxy_authz_leak",
        repo="https://github.com/psf/requests",
        commit="0e322af87745eff34caffe4df68456ebc20d9068",
        issue_text=(
            "When requests follows a redirect to a host different from "
            "the original request, the Proxy-Authorization header is "
            "still sent. This can leak credentials to the redirect "
            "target."
        ),
        culprit_file="src/requests/sessions.py",
        culprit_symbol="Session.rebuild_proxies",
        line_range=(280, 340),
        required_failure_keywords=(
            "proxy-authorization",
            "redirect",
            "leak",
        ),
        difficulty="medium",
        bucket="header_leak",
        source_url="https://github.com/psf/requests/security/advisories/GHSA-9wx4-h78v-vm56",
    ),
    # urllib3: Cookie header forwarded across cross-origin redirect.
    # Advisory is real; SHA is unverified.
    ChallengeRow(
        id="UNVERIFIED_urllib3_cookie_redirect",
        repo="https://github.com/urllib3/urllib3",
        commit="ddf7361ac0c4cbb2c3d76ec24a1cb220b35eb2e5",
        issue_text=(
            "Cookie HTTP headers are not stripped when following a "
            "cross-origin redirect, leaking session cookies to the "
            "redirect target."
        ),
        culprit_file="src/urllib3/_request_methods.py",
        culprit_symbol="RequestMethods.request",
        line_range=(150, 220),
        required_failure_keywords=("cookie", "redirect", "strip"),
        difficulty="medium",
        bucket="header_leak",
        source_url="https://github.com/urllib3/urllib3/security/advisories/GHSA-v845-jxx5-vc9f",
    ),
    # urllib3: Proxy-Authorization forwarded on HTTPS to HTTP downgrade.
    # Advisory is real; SHA is unverified.
    ChallengeRow(
        id="UNVERIFIED_urllib3_authz_downgrade",
        repo="https://github.com/urllib3/urllib3",
        commit="2e7a24d08713a0131f0b3c7197889466ec486db5",
        issue_text=(
            "Proxy-Authorization header is forwarded when an HTTPS "
            "request is redirected to an HTTP destination, exposing "
            "credentials in cleartext."
        ),
        culprit_file="src/urllib3/util/retry.py",
        culprit_symbol="Retry.remove_headers_on_redirect",
        line_range=(60, 120),
        required_failure_keywords=("proxy", "https", "downgrade"),
        difficulty="hard",
        bucket="header_leak",
        source_url="https://github.com/urllib3/urllib3/security/advisories/GHSA-34jh-p97f-mpxf",
    ),
    # flask: session cookie default SameSite (file-level, no symbol).
    # Reflects a real design discussion in Flask, but no specific
    # fix commit; using main HEAD as the source pointer. Stays as a
    # fixture-only example because the bug claim is more design
    # critique than a discrete commit fix.
    ChallengeRow(
        id="UNVERIFIED_flask_session_samesite_default",
        repo="https://github.com/pallets/flask",
        commit="b78b5a210bde2e9b80e9f6a4f24c61c2f5e9c0d1",
        issue_text=(
            "The default Flask session cookie does not set SameSite, "
            "leaving applications open to CSRF unless the developer "
            "remembers to configure it explicitly."
        ),
        culprit_file="src/flask/sessions.py",
        culprit_symbol=None,
        line_range=(1, 50),
        required_failure_keywords=("samesite", "cookie", "default"),
        difficulty="easy",
        bucket="config_default",
        source_url="https://github.com/pallets/flask/blob/main/src/flask/sessions.py",
    ),
    # click: mutable default in Option.__init__ (real bug class, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_click_mutable_default_envvar",
        repo="https://github.com/pallets/click",
        commit="d0af32d4f7c0c2d9c1e3c08b8d5e5d0e0f0c0a0b",
        issue_text=(
            "Click Option accepts a mutable list as the default value "
            "and the same list is shared across all invocations of "
            "commands that use that option."
        ),
        culprit_file="src/click/core.py",
        culprit_symbol="Option.__init__",
        line_range=(2200, 2280),
        required_failure_keywords=("mutable", "default", "shared"),
        difficulty="medium",
        bucket="mutable_default",
        source_url="https://github.com/pallets/click/pull/1556",
    ),
    # cryptography: off-by-one in PKCS7 unpadding (real bug class, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_cryptography_padding_off_by_one",
        repo="https://github.com/pyca/cryptography",
        commit="7a3c2f9d2c8e1b5d4a6f8e9c0d1b2a3c4e5f6789",
        issue_text=(
            "PKCS7 unpadding can read one byte past the end of the "
            "buffer when the padding byte value equals the block size."
        ),
        culprit_file="src/cryptography/hazmat/primitives/padding.py",
        culprit_symbol="PKCS7.unpadder",
        line_range=(80, 140),
        required_failure_keywords=("padding", "off-by-one", "buffer"),
        difficulty="hard",
        bucket="off_by_one",
        source_url="https://github.com/pyca/cryptography/blob/main/src/cryptography/hazmat/primitives/padding.py",
    ),
    # fastapi: dependency override leak across requests (real issue, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_fastapi_dependency_override_leak",
        repo="https://github.com/tiangolo/fastapi",
        commit="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        issue_text=(
            "When an application overrides a dependency during testing, "
            "the override persists into the next request because the "
            "override dict is module-level and not cleared between "
            "requests."
        ),
        culprit_file="fastapi/dependencies/utils.py",
        culprit_symbol="solve_dependencies",
        line_range=(420, 510),
        required_failure_keywords=("override", "dependency", "leak"),
        difficulty="medium",
        bucket="state_leak",
        source_url="https://github.com/tiangolo/fastapi/issues/5688",
    ),
    # pydantic: validator skipped on default (file-level, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_pydantic_validator_skips_default",
        repo="https://github.com/pydantic/pydantic",
        commit="b5d4c3a2e1f0d9c8b7a6f5e4d3c2b1a0e9d8c7b6",
        issue_text=(
            "A field validator does not run when the field uses its "
            "default value, only when the value is explicitly supplied. "
            "This contradicts the documentation."
        ),
        culprit_file="pydantic/fields.py",
        culprit_symbol=None,
        line_range=(100, 180),
        required_failure_keywords=("validator", "default", "skip"),
        difficulty="medium",
        bucket="config_default",
        source_url="https://github.com/pydantic/pydantic/blob/main/pydantic/fields.py",
    ),
)
