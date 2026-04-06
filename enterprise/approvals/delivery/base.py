"""ApprovalDelivery protocol -- how approval notifications reach humans."""

from typing import Protocol

from ..models import ApprovalRequest


class ApprovalDelivery(Protocol):
    """Protocol for delivering approval notifications to human reviewers.

    Implementations must not raise -- log and swallow errors.
    """

    async def send(self, request: ApprovalRequest) -> None:
        """Deliver an approval notification for the given request."""
        ...
