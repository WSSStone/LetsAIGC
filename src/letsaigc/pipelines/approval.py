"""Expert entrypoint only. This module is not registered as an Agent capability."""

import getpass

from ..schemas.pipeline import ApprovalRequest, digest
from .ledger import Ledger


def approve(ledger: Ledger, task_id: str, fingerprint: str, *, reject: bool = False) -> ApprovalRequest:
    decision = "reject" if reject else "approve"
    request = ApprovalRequest(
        task_id=task_id,
        plan_fingerprint=fingerprint,
        decision=decision,
        request_id="approval-" + digest([task_id, fingerprint, decision])[:40],
    )
    ledger.record_approval(request, actor=getpass.getuser())
    return request
