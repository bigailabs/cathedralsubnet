"""bug_isolation_v1 challenge corpus.

The corpus is the hidden ground truth Cathedral scores miner claims
against. Public fields (repo, commit, paraphrased issue_text) go to
the miner; hidden fields (culprit_file, culprit_symbol, line_range,
required_failure_keywords) stay on the publisher.

Every seed row MUST cite a real fix commit or GHSA URL in
``source_url`` so a reviewer can independently verify the oracle is
real. No fake SHAs, no invented bugs, no guessed line ranges. If a
proposed row cannot be verified against an upstream source, drop it.
"""

from cathedral.v3.corpus.sampler import sample_challenge_id_for_hotkey
from cathedral.v3.corpus.schema import ChallengeRow

__all__ = [
    "ChallengeRow",
    "sample_challenge_id_for_hotkey",
]
