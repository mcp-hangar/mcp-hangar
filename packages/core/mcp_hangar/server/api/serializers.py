"""JSON serializers for API responses.

Provides:
- HangarJSONEncoder: Custom JSON encoder handling datetime, Enum, set, and to_dict objects
- HangarJSONResponse: JSONResponse subclass using HangarJSONEncoder
- serialize_provider_summary: Convert ProviderSummary to JSON-safe dict
- serialize_provider_details: Convert ProviderDetails to JSON-safe dict
- serialize_tool_info: Convert ToolInfo to JSON-safe dict
- serialize_health_info: Convert HealthInfo to JSON-safe dict
"""

import json
from datetime import datetime
from enum import Enum
from typing import Any, TYPE_CHECKING

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from ...application.read_models.provider_views import (
        HealthInfo,
        ProviderDetails,
        ProviderSummary,
        ToolInfo,
    )


class HangarJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for domain objects.

    Handles types that the default encoder cannot serialize:
    - datetime: serialized as ISO 8601 string
    - Enum: serialized as .value
    - set: serialized as sorted list
    - Objects with .to_dict(): serialized by calling it
    """

    def default(self, obj: Any) -> Any:
        """Serialize objects that the default encoder cannot handle.

        Args:
            obj: The object to serialize.

        Returns:
            JSON-serializable representation.
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, set):
            return sorted(obj, key=str)
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return super().default(obj)


class HangarJSONResponse(JSONResponse):
    """JSONResponse that uses HangarJSONEncoder for serialization.

    All API endpoints should return HangarJSONResponse to ensure
    domain objects (datetime, Enum, etc.) are properly serialized.
    """

    def render(self, content: Any) -> bytes:
        """Render content to bytes using HangarJSONEncoder.

        Args:
            content: The content to serialize.

        Returns:
            UTF-8 encoded JSON bytes.
        """
        return json.dumps(
            content,
            cls=HangarJSONEncoder,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


def serialize_provider_summary(summary: "ProviderSummary") -> dict[str, Any]:
    """Convert ProviderSummary read model to JSON-safe dict.

    Args:
        summary: ProviderSummary read model instance.

    Returns:
        JSON-safe dictionary representation.
    """
    return summary.to_dict()


def serialize_provider_details(details: "ProviderDetails") -> dict[str, Any]:
    """Convert ProviderDetails read model to JSON-safe dict.

    Args:
        details: ProviderDetails read model instance.

    Returns:
        JSON-safe dictionary representation.
    """
    return details.to_dict()


def serialize_tool_info(tool: "ToolInfo") -> dict[str, Any]:
    """Convert ToolInfo read model to JSON-safe dict.

    Args:
        tool: ToolInfo read model instance.

    Returns:
        JSON-safe dictionary representation.
    """
    return tool.to_dict()


def serialize_health_info(health: "HealthInfo") -> dict[str, Any]:
    """Convert HealthInfo read model to JSON-safe dict.

    Args:
        health: HealthInfo read model instance.

    Returns:
        JSON-safe dictionary representation.
    """
    return health.to_dict()
