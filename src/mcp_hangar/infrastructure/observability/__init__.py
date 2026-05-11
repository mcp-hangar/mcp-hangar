"""Observability infrastructure adapters."""

import importlib

_ENTERPRISE_LANGFUSE_SYMBOLS = {
    "LangfuseAdapter": "mcp_hangar.integrations.langfuse",
    "LangfuseConfig": "mcp_hangar.integrations.langfuse",
    "LangfuseObservabilityAdapter": "mcp_hangar.integrations.langfuse",
    "LangfuseSpanHandle": "mcp_hangar.integrations.langfuse",
}

__all__: list[str] = list(_ENTERPRISE_LANGFUSE_SYMBOLS)


def __getattr__(name: str):  # noqa: ANN001
    module_name = _ENTERPRISE_LANGFUSE_SYMBOLS.get(name)
    if module_name is not None:
        try:
            return getattr(importlib.import_module(module_name), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
