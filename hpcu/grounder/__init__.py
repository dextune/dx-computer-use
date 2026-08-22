"""Grounder — connect natural-language target queries to element IDs.

Grounding is OS-agnostic: it resolves against the common Scene snapshot
and never calls platform APIs.
"""

from hpcu.grounder.grounder import Grounder, GroundingCandidate, GroundingResult

__all__ = ["Grounder", "GroundingCandidate", "GroundingResult"]
