"""Domain errors.

CLAUDE.md rule 6: fail honestly, degrade gracefully. Each error carries a
message written for an investigator reading it on screen, not a stack trace.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class CaseNotFound(DomainError):
    status_code = 404
    code = "case_not_found"

    def __init__(self, case_id: str):
        super().__init__(
            f"No case with id {case_id} exists in this database.",
            {"case_id": case_id},
        )


class FindingNotFound(DomainError):
    status_code = 404
    code = "finding_not_found"

    def __init__(self, finding_id: str):
        super().__init__(
            f"No finding with id {finding_id} exists for this case.",
            {"finding_id": finding_id},
        )


class EvidenceNotFound(DomainError):
    status_code = 404
    code = "evidence_not_found"

    def __init__(self, evidence_id: str):
        super().__init__(
            f"No evidence record with id {evidence_id} exists for this case.",
            {"evidence_id": evidence_id},
        )


class InvalidRequest(DomainError):
    status_code = 422
    code = "invalid_request"


class CaseNotInvestigated(DomainError):
    status_code = 409
    code = "case_not_investigated"

    def __init__(self, case_id: str):
        super().__init__(
            "This case has not been investigated yet. Run the investigation "
            "before requesting this view.",
            {"case_id": case_id},
        )


class NoDataAvailable(DomainError):
    """Raised when neither the cache nor a reachable upstream can serve an address.

    This is a legitimate outcome, not a bug: the demo path runs from cache, and
    an address that was never prefetched simply has no data here.
    """

    status_code = 409
    code = "no_data_available"

    def __init__(self, address: str, chain: str, note: str):
        super().__init__(
            f"No transaction data is available for {address} on {chain}. {note}",
            {"address": address, "chain": chain, "note": note},
        )
