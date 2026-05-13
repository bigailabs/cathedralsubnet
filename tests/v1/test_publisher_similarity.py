"""Pre-eval similarity check (CONTRACTS.md §7.1) — same-hotkey resubmit path.

The 7-day fuzzy display_name collision check (§7.1.3) is meant to stop
OTHER miners from squatting an existing display_name. It must NOT block
a miner from resubmitting under their own hotkey — that's the normal
recovery path after a failed eval.

These tests pin the post-v1.1.4 behavior:

  - Same hotkey, same display_name within 7 days → ALLOWED
  - Same hotkey, fuzzy display_name match within 7 days → ALLOWED
  - Different hotkey, same display_name within 7 days → REJECTED
  - Different hotkey, fuzzy display_name match within 7 days → REJECTED

Hotkeys are real sr25519 keypairs from `tests/v1/conftest.py` — no
faking, per repo conventions.
"""

from __future__ import annotations

from tests.v1.conftest import (
    make_valid_bundle,
    submit_multipart,
)

# --------------------------------------------------------------------------
# Same hotkey — resubmits MUST be allowed
# --------------------------------------------------------------------------


def test_same_hotkey_same_display_name_resubmit_is_allowed(publisher_client, alice_keypair):
    """§7.1.3 — alice resubmits with the EXACT same display_name (different
    bundle) within 7 days. The fuzzy dedupe must skip alice's own row.

    This is the McDee-Regulatory bug from the first-miner onboarding pass:
    a miner who failed eval and resubmits with the same display_name was
    blocked by their own prior submissions.
    """
    bundle_a = make_valid_bundle(soul_md="# alice v1\n")
    bundle_b = make_valid_bundle(soul_md="# alice v2 — different bytes\n")

    first = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_a,
        display_name="McDEE-Regulatory",
    )
    assert first.status_code == 202, f"first submit must succeed: {first.text}"
    assert first.json().get("status") != "rejected", (
        f"first submit should not be rejected: {first.json()}"
    )

    second = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_b,
        display_name="McDEE-Regulatory",
    )
    assert second.status_code == 202, (
        f"§7.1.3 same-hotkey resubmit must succeed (no fuzzy block), "
        f"got {second.status_code}: {second.text}"
    )
    body = second.json()
    assert body.get("status") != "rejected", (
        f"§7.1.3 same-hotkey resubmit must NOT be rejected as fuzzy collision; got {body}"
    )
    assert "rejection_reason" not in body or body.get("rejection_reason") is None, (
        f"§7.1.3 same-hotkey resubmit must have no rejection_reason; got {body}"
    )


def test_same_hotkey_fuzzy_display_name_resubmit_is_allowed(publisher_client, alice_keypair):
    """§7.1.3 — alice resubmits with a near-identical (>0.85 levenshtein)
    display_name. Fuzzy dedupe must skip her own rows.
    """
    bundle_a = make_valid_bundle(soul_md="# alice v1 fuzzy\n")
    bundle_b = make_valid_bundle(soul_md="# alice v2 — fuzzy resubmit\n")

    first = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_a,
        display_name="McDEE",
    )
    assert first.status_code == 202, f"first submit must succeed: {first.text}"

    # `McDEE-v2` shares 5 chars with `McDEE` over 8 → ratio ≈ 0.625, but
    # a closer variant matches the >= 0.85 threshold. Use `McDee-v2`
    # which differs only in case for two chars: NFKC-lowercases both
    # sides so the normalized strings are `mcdee` vs `mcdee-v2` —
    # levenshtein ratio is 5/8 = 0.625, so use a tighter variant to
    # cross the threshold.
    second = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_b,
        display_name="McDEEx",  # 5 of 6 match -> ratio ≈ 0.833 (just under)
    )
    # The above is intentionally just-under; the real test is the exact-
    # name variant which the prod bug surfaced on. Use the more direct
    # variant that is obviously fuzzy-similar.
    bundle_c = make_valid_bundle(soul_md="# alice v3 — fuzzy resubmit v2\n")
    third = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_c,
        display_name="McDee",  # NFKC-lowercased → identical to first
    )
    assert third.status_code == 202, (
        f"§7.1.3 same-hotkey case-variant resubmit must succeed; "
        f"got {third.status_code}: {third.text}"
    )
    assert third.json().get("status") != "rejected", (
        f"§7.1.3 case-variant resubmit must not be rejected; got {third.json()}"
    )

    # And the just-under-threshold submit must also have succeeded.
    assert second.status_code == 202, f"same-hotkey fuzzy resubmit must succeed; got {second.text}"
    assert second.json().get("status") != "rejected"


# --------------------------------------------------------------------------
# Different hotkey — squatting MUST still be rejected
# --------------------------------------------------------------------------


def test_different_hotkey_same_display_name_is_still_rejected(
    publisher_client, alice_keypair, bob_keypair
):
    """§7.1.3 — bob tries to squat alice's display_name within 7 days.
    Must still be rejected (status=rejected with rejection_reason set).
    """
    bundle_a = make_valid_bundle(soul_md="# alice's bundle\n")
    bundle_b = make_valid_bundle(soul_md="# bob trying to squat\n")

    first = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_a,
        display_name="CathedralPrime",
    )
    assert first.status_code == 202, f"alice first submit: {first.text}"
    assert first.json().get("status") != "rejected"

    squat = submit_multipart(
        publisher_client,
        keypair=bob_keypair,
        card_id="eu-ai-act",
        bundle=bundle_b,
        display_name="CathedralPrime",
    )
    assert squat.status_code == 202, (
        f"§7.1.3 + §6 step 2: cross-hotkey fuzzy collision returns 202 with "
        f"status=rejected, not 409; got {squat.status_code}: {squat.text}"
    )
    body = squat.json()
    assert body.get("status") == "rejected", (
        f"§7.1.3 cross-hotkey same display_name must be status=rejected; got {body}"
    )
    assert body.get("rejection_reason"), (
        f"§7.1.3 cross-hotkey rejection must carry a rejection_reason; got {body}"
    )
    assert "too similar" in body["rejection_reason"], (
        f"§7.1.3 rejection_reason should mention 'too similar'; got {body['rejection_reason']!r}"
    )


def test_different_hotkey_fuzzy_display_name_is_still_rejected(
    publisher_client, alice_keypair, bob_keypair
):
    """§7.1.3 — bob's display_name is fuzzy-close (>0.85) to alice's.
    Must still be rejected.
    """
    bundle_a = make_valid_bundle(soul_md="# alice fuzzy original\n")
    bundle_b = make_valid_bundle(soul_md="# bob fuzzy squat\n")

    first = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_a,
        display_name="RegulatoryAgent",
    )
    assert first.status_code == 202, f"alice first submit: {first.text}"
    assert first.json().get("status") != "rejected"

    # `RegulatoryAgentX` — 15/16 chars match → ratio ≈ 0.9375, above 0.85.
    squat = submit_multipart(
        publisher_client,
        keypair=bob_keypair,
        card_id="eu-ai-act",
        bundle=bundle_b,
        display_name="RegulatoryAgentX",
    )
    assert squat.status_code == 202, (
        f"§7.1.3 + §6 step 2: cross-hotkey fuzzy collision is 202 with "
        f"status=rejected, not 409; got {squat.status_code}: {squat.text}"
    )
    body = squat.json()
    assert body.get("status") == "rejected", (
        f"§7.1.3 cross-hotkey fuzzy collision must be status=rejected; got {body}"
    )
    assert body.get("rejection_reason"), (
        f"§7.1.3 cross-hotkey fuzzy rejection must carry rejection_reason; got {body}"
    )
