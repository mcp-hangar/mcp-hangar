"""Slack webhook delivery for approval notifications.

Sends Block Kit messages to a configured Slack webhook URL with
Approve/Deny action buttons. Arguments are sanitized via key-based
redaction and truncated to 1000 chars before delivery.
"""

import json
from typing import Any

from mcp_hangar.logging_config import get_logger

from ..models import ApprovalRequest

logger = get_logger(__name__)

# Keys matching these patterns have their values redacted
_SENSITIVE_PATTERNS = {"password", "token", "secret", "key", "auth", "credential"}
_MAX_ARGS_LENGTH = 1000


def _sanitize_for_display(arguments: dict[str, Any]) -> str:
    """Sanitize and truncate arguments for Slack display."""
    sanitized = {}
    for k, v in arguments.items():
        if any(p in k.lower() for p in _SENSITIVE_PATTERNS):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    text = json.dumps(sanitized, indent=2, default=str)
    if len(text) > _MAX_ARGS_LENGTH:
        text = text[:_MAX_ARGS_LENGTH] + "\n... (truncated)"
    return text


def _build_slack_blocks(request: ApprovalRequest) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for an approval request."""
    args_text = _sanitize_for_display(request.arguments)
    expires_in = max(
        0,
        int(
            (request.expires_at - request.requested_at).total_seconds() / 60
        ),
    )

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Approval Required",
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Provider:* `{request.provider_id}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Tool:* `{request.tool_name}`",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Arguments:*\n```{args_text}```",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Expires in {expires_in} minutes | ID: `{request.approval_id}`",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_{request.approval_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "action_id": f"deny_{request.approval_id}",
                },
            ],
        },
    ]


class SlackApprovalDelivery:
    """Sends approval notifications to Slack via incoming webhook."""

    def __init__(self, webhook_url: str, signing_secret: str) -> None:
        self._webhook_url = webhook_url
        self._signing_secret = signing_secret

    async def send(self, request: ApprovalRequest) -> None:
        """Send approval notification to Slack. Logs and swallows errors."""
        try:
            import httpx

            blocks = _build_slack_blocks(request)
            payload = {"blocks": blocks}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._webhook_url,
                    json=payload,
                )
                if response.status_code != 200:
                    logger.warning(
                        "slack_delivery_non_200",
                        approval_id=request.approval_id,
                        status=response.status_code,
                        body=response.text[:200],
                    )
                else:
                    logger.info(
                        "slack_approval_delivered",
                        approval_id=request.approval_id,
                        tool=request.tool_name,
                    )
        except ImportError:
            logger.error("slack_delivery_requires_httpx")
        except Exception:
            logger.warning(
                "slack_delivery_failed",
                approval_id=request.approval_id,
                exc_info=True,
            )
