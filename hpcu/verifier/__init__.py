"""Verifier — evidence-based completion and postcondition checks.

A task is complete only when its EvidenceContract is proven against the
current Scene; nothing is reported complete on the model's word alone.
"""

from hpcu.verifier.verifier import Verifier

__all__ = ["Verifier"]
