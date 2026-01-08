"""Provider interaction tools: tools, invoke, details.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context, FastMCP

from ...application.commands import InvokeToolCommand, StartProviderCommand
from ...application.mcp.tooling import chain_validators, key_registry_invoke, mcp_tool_wrapper
from ...domain.model import ProviderGroup
from ...errors import (
    ProviderNotFoundError as HangarProviderNotFoundError,
    map_exception_to_hangar_error,
)
from ...infrastructure.async_executor import submit_async
from ...infrastructure.query_bus import GetProviderQuery, GetProviderToolsQuery
from ...progress import (
    ProgressCallback,
    ProgressStage,
    ProgressTracker,
    create_progress_tracker,
    get_stage_message,
)
from ...retry import RetryPolicy, get_retry_policy, retry_sync
from ..context import get_context
from ..validation import (
    check_rate_limit,
    tool_error_hook,
    tool_error_mapper,
    validate_arguments_input,
    validate_provider_id_input,
    validate_timeout_input,
    validate_tool_name_input,
)

# =============================================================================
# Constants
# =============================================================================

DEFAULT_GROUP_RETRY_ATTEMPTS = 2
"""Number of retry attempts when invoking tool on group members."""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Default timeout for tool invocation."""

# =============================================================================
# Helper Functions
# =============================================================================


def _extract_error_text(content: Any) -> str:
    """Extract error message text from MCP content array.

    MCP content can be:
    - A list of dicts with type/text: [{"type": "text", "text": "Error: ..."}]
    - A string
    - A dict with text field

    Args:
        content: MCP content field (can be list, dict, or string)

    Returns:
        Extracted error text string
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # Extract text from content items
        texts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text", "")
                if text:
                    texts.append(text)
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts) if texts else "Unknown error"

    if isinstance(content, dict):
        return content.get("text", content.get("message", str(content)))

    return str(content) if content else "Unknown error"


def _submit_audit_log(
    provider: str,
    tool: str,
    arguments: Dict[str, Any],
    elapsed_ms: float,
    success: bool,
    result_summary: Optional[str] = None,
    error_message: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Submit audit log entry to knowledge base asynchronously.

    This is a fire-and-forget operation that won't block the main thread
    and won't fail the invocation if audit logging fails.

    Args:
        provider: Provider ID
        tool: Tool name
        arguments: Tool arguments
        elapsed_ms: Operation duration in milliseconds
        success: Whether the operation succeeded
        result_summary: Optional result summary (truncated)
        error_message: Optional error message (truncated)
        correlation_id: Optional correlation ID for tracing
    """
    from ...infrastructure.knowledge_base import is_available, audit_log

    if not is_available():
        return

    async def _do_audit():
        await audit_log(
            event_type="tool_invocation",
            provider=provider,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            duration_ms=int(elapsed_ms),
            success=success,
            error_message=error_message,
            correlation_id=correlation_id,
        )

    submit_async(_do_audit())


def _get_tools_for_group(provider: str) -> Dict[str, Any]:
    """Get tools for a provider group."""
    ctx = get_context()
    group = ctx.get_group(provider)
    selected = group.select_member()

    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {provider}")

    ctx.command_bus.send(StartProviderCommand(provider_id=selected.provider_id))
    query = GetProviderToolsQuery(provider_id=selected.provider_id)
    tools = ctx.query_bus.execute(query)

    return {
        "provider": provider,
        "group": True,
        "tools": [t.to_dict() for t in tools],
    }


def _get_tools_for_provider(provider: str) -> Dict[str, Any]:
    """Get tools for a single provider."""
    ctx = get_context()
    provider_obj = ctx.get_provider(provider)

    # If provider has predefined tools, return them without starting
    if provider_obj.has_tools:
        tools = provider_obj.tools.list_tools()
        return {
            "provider": provider,
            "state": provider_obj.state.value,
            "predefined": provider_obj.tools_predefined,
            "tools": [t.to_dict() for t in tools],
        }

    # Start provider and discover tools
    ctx.command_bus.send(StartProviderCommand(provider_id=provider))
    query = GetProviderToolsQuery(provider_id=provider)
    tools = ctx.query_bus.execute(query)

    return {
        "provider": provider,
        "state": provider_obj.state.value,
        "predefined": False,
        "tools": [t.to_dict() for t in tools],
    }


def _invoke_on_provider(
    provider: str,
    tool: str,
    arguments: Dict,
    timeout: float,
    progress: Optional[ProgressTracker] = None,
) -> Dict[str, Any]:
    """Invoke tool on a single provider."""
    ctx = get_context()

    # Check if provider needs cold start
    provider_obj = ctx.get_provider(provider)
    is_cold_start = provider_obj and provider_obj.state.value == "cold"

    if is_cold_start and progress:
        progress.report(ProgressStage.LAUNCHING, f"Starting {provider_obj.mode} provider...")

    # Report ready state after provider is available (launched or already warm)
    if progress and not is_cold_start:
        # Already warm - skip to ready
        pass  # ready was already reported in caller
    elif progress:
        # Cold start completed - report ready
        progress.report(ProgressStage.READY, "Provider ready")

    if progress:
        progress.report(
            ProgressStage.EXECUTING,
            get_stage_message(ProgressStage.EXECUTING, tool=tool)
        )

    command = InvokeToolCommand(
        provider_id=provider,
        tool_name=tool,
        arguments=arguments,
        timeout=timeout,
    )
    result = ctx.command_bus.send(command)

    if progress:
        progress.report(ProgressStage.PROCESSING, "Processing response...")

    return result


def _invoke_on_group(
    group_id: str,
    tool: str,
    arguments: Dict,
    timeout: float,
    progress: Optional[ProgressTracker] = None,
) -> Dict[str, Any]:
    """Invoke a tool on a provider group with load balancing."""
    ctx = get_context()
    group = ctx.get_group(group_id)

    if not group.is_available:
        raise ValueError(f"group_not_available: {group_id} (state={group.state.value})")

    selected = group.select_member()
    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {group_id}")

    return _invoke_with_retry(group, tool, arguments, timeout, progress=progress)


def _invoke_with_retry(
    group: ProviderGroup,
    tool: str,
    arguments: Dict,
    timeout: float,
    max_attempts: int = DEFAULT_GROUP_RETRY_ATTEMPTS,
    progress: Optional[ProgressTracker] = None,
) -> Dict[str, Any]:
    """Invoke tool with retry on different group members."""
    first_error: Optional[Exception] = None
    tried_members: set = set()

    for attempt in range(max_attempts):
        selected = group.select_member()
        if not selected or selected.provider_id in tried_members:
            break

        tried_members.add(selected.provider_id)

        try:
            result = _invoke_on_provider(selected.provider_id, tool, arguments, timeout, progress)
            group.report_success(selected.provider_id)
            return result
        except Exception as e:
            group.report_failure(selected.provider_id)
            first_error = first_error or e

            if progress and attempt < max_attempts - 1:
                progress.report(
                    ProgressStage.RETRYING,
                    get_stage_message(
                        ProgressStage.RETRYING,
                        attempt=attempt + 2,
                        max_attempts=max_attempts,
                    )
                )

    raise first_error or ValueError("no_healthy_members_in_group")


# =============================================================================
# Tool Registration
# =============================================================================


def register_provider_tools(mcp: FastMCP) -> None:
    """Register provider interaction tools with MCP server."""

    @mcp.tool(name="registry_tools")
    @mcp_tool_wrapper(
        tool_name="registry_tools",
        rate_limit_key=lambda provider: f"registry_tools:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_tools(provider: str) -> dict:
        """
        Get detailed tool schemas for a provider.

        This is a QUERY operation with potential side-effect (starting provider).

        Args:
            provider: Provider ID

        Returns:
            Dictionary with provider ID and list of tool schemas

        Raises:
            ValueError: If provider ID is unknown or invalid
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return _get_tools_for_group(provider)

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        return _get_tools_for_provider(provider)

    @mcp.tool(name="registry_invoke")
    @mcp_tool_wrapper(
        tool_name="registry_invoke",
        rate_limit_key=key_registry_invoke,
        check_rate_limit=check_rate_limit,
        validate=chain_validators(
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_provider_id_input(
                provider
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_tool_name_input(tool),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_arguments_input(
                arguments or {}
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_timeout_input(timeout),
        ),
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_invoke(
        provider: str,
        tool: str,
        arguments: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict:
        """
        Invoke a tool on a provider or provider group.

        This is a COMMAND operation - it may have side effects.

        Args:
            provider: Provider ID or Group ID
            tool: Tool name
            arguments: Tool arguments (default: empty dict)
            timeout: Timeout in seconds (default: 30.0)

        Returns:
            Tool result

        Raises:
            ValueError: If provider ID is unknown or inputs are invalid
        """
        ctx = get_context()
        args = arguments or {}

        if ctx.group_exists(provider):
            return _invoke_on_group(provider, tool, args, timeout)

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        return _invoke_on_provider(provider, tool, args, timeout)

    # =========================================================================
    # Enhanced invoke with retry and progress support
    # =========================================================================

    def _invoke_with_full_retry(
        provider: str,
        tool: str,
        arguments: Dict[str, Any],
        timeout: float,
        retry_policy: Optional[RetryPolicy] = None,
        progress_callback: Optional[ProgressCallback] = None,
        include_progress: bool = True,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal implementation of invoke with retry and progress.

        Progress is always tracked and logged. If include_progress=True,
        progress events are included in the response under _progress key.
        """
        from ...logging_config import get_logger
        from ...errors import ErrorClassifier
        logger = get_logger(__name__)

        ctx = get_context()
        args = arguments or {}
        correlation_id = correlation_id or str(uuid.uuid4())

        # Get retry policy (provider-specific or default)
        policy = retry_policy or get_retry_policy(provider)

        # Always create progress tracker for logging
        # User callback is optional (for programmatic use)
        progress_events = []

        def log_progress(stage: str, message: str, elapsed_ms: float):
            """Log progress and collect events."""
            event = {
                "stage": stage,
                "message": message,
                "elapsed_ms": round(elapsed_ms, 2),
            }
            progress_events.append(event)
            logger.info(
                "operation_progress",
                provider=provider,
                tool=tool,
                correlation_id=correlation_id,
                **event,
            )
            # Also call user callback if provided
            if progress_callback:
                try:
                    progress_callback(stage, message, elapsed_ms)
                except Exception:
                    pass

        progress = create_progress_tracker(
            provider=provider,
            operation=tool,
            callback=log_progress,
            correlation_id=correlation_id,
        )

        # Report initial state
        provider_obj = ctx.get_provider(provider) if ctx.provider_exists(provider) else None
        if provider_obj and provider_obj.state.value == "cold":
            progress.report(ProgressStage.COLD_START, "Provider is cold, launching...")
        else:
            progress.report(ProgressStage.READY, "Starting operation...")

        def do_invoke():
            if ctx.group_exists(provider):
                return _invoke_on_group(provider, tool, args, timeout, progress)

            if not ctx.provider_exists(provider):
                available = list(ctx.repository.get_all().keys())
                raise HangarProviderNotFoundError(
                    message=f"Provider '{provider}' not found",
                    provider=provider,
                    operation="invoke",
                    available_providers=available,
                )

            return _invoke_on_provider(provider, tool, args, timeout, progress)

        # Execute with retry
        start_time = time.time()
        result = retry_sync(
            operation=do_invoke,
            policy=policy,
            provider=provider,
            operation_name=tool,
            on_retry=lambda attempt, err, delay: progress.report(
                ProgressStage.RETRYING,
                f"Retry {attempt}/{policy.max_attempts} in {delay:.1f}s: {str(err)[:50]}",
            ),
        )

        elapsed_ms = (time.time() - start_time) * 1000

        # Submit audit log (fire-and-forget, won't block or fail invocation)
        _submit_audit_log(
            provider=provider,
            tool=tool,
            arguments=args,
            elapsed_ms=elapsed_ms,
            success=result.success,
            result_summary=str(result.result)[:200] if result.success else None,
            error_message=str(result.final_error)[:500] if not result.success else None,
            correlation_id=correlation_id,
        )

        if result.success:
            progress.complete(result.result)

            response: Dict[str, Any] = {
                **result.result,
                "_retry_metadata": {
                    "correlation_id": correlation_id,
                    "attempts": result.attempt_count,
                    "total_time_ms": round(elapsed_ms, 2),
                    "retries": [a.error_type for a in result.attempts],
                },
            }

            # Check if result contains isError: true (provider returned error in response)
            # This is different from an exception - the provider executed successfully
            # but returned an error response (e.g., division by zero)
            if result.result.get("isError"):
                # Extract error message from content
                error_text = _extract_error_text(result.result.get("content", []))
                error_classification = ErrorClassifier.classify(Exception(error_text))

                response["_retry_metadata"]["final_error_reason"] = error_classification["final_error_reason"]
                response["_retry_metadata"]["recovery_hints"] = error_classification["recovery_hints"]

            # Include progress events if requested
            if include_progress:
                response["_progress"] = progress_events

            return response
        else:
            # Classify the error for enriched metadata
            error_classification = ErrorClassifier.classify(result.final_error)

            # Map error to HangarError for better UX
            hangar_error = map_exception_to_hangar_error(
                result.final_error,
                provider=provider,
                operation=tool,
                context={
                    "arguments": args,
                    "timeout": timeout,
                    "attempts": result.attempt_count,
                    "progress": progress_events,
                },
            )
            progress.fail(hangar_error)

            # Return error response with enriched metadata (don't raise)
            error_response: Dict[str, Any] = {
                "content": f"Error executing tool {tool}: {str(result.final_error)}",
                "isError": True,
                "_retry_metadata": {
                    "correlation_id": correlation_id,
                    "attempts": result.attempt_count,
                    "total_time_ms": round(elapsed_ms, 2),
                    "retries": [a.error_type for a in result.attempts],
                    "final_error_reason": error_classification["final_error_reason"],
                    "recovery_hints": error_classification["recovery_hints"],
                },
            }

            # Include progress events if requested
            if include_progress:
                error_response["_progress"] = progress_events

            return error_response

    @mcp.tool(name="registry_invoke_ex")
    @mcp_tool_wrapper(
        tool_name="registry_invoke_ex",
        rate_limit_key=key_registry_invoke,
        check_rate_limit=check_rate_limit,
        validate=chain_validators(
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS, **kw: validate_provider_id_input(
                provider
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS, **kw: validate_tool_name_input(tool),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS, **kw: validate_arguments_input(
                arguments or {}
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS, **kw: validate_timeout_input(timeout),
        ),
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def registry_invoke_ex(
        provider: str,
        tool: str,
        arguments: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 3,
        retry_on_error: bool = True,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Invoke a tool with automatic retry on transient failures.

        Extended version of registry_invoke with:
        - Automatic retry with exponential backoff
        - Rich error messages with recovery hints
        - Retry metadata in response
        - Correlation ID for tracing

        Args:
            provider: Provider ID or Group ID
            tool: Tool name
            arguments: Tool arguments (default: empty dict)
            timeout: Timeout in seconds (default: 30.0)
            max_retries: Maximum retry attempts (default: 3)
            retry_on_error: Whether to retry on transient errors (default: True)
            correlation_id: Optional correlation ID for tracing (auto-generated UUID if not provided)

        Returns:
            Tool result with _retry_metadata field containing:
            - correlation_id: Trace ID for this operation
            - attempts: Number of attempts made
            - total_time_ms: Total execution time
            - retries: List of error types from retries
            - final_error_reason: (on error) Classification of the error
            - recovery_hints: (on error) Actionable steps to resolve

        Raises:
            HangarError: Rich error with recovery hints
        """
        policy = None
        if retry_on_error:
            policy = RetryPolicy(max_attempts=max_retries)
        else:
            policy = RetryPolicy(max_attempts=1)

        return _invoke_with_full_retry(
            provider=provider,
            tool=tool,
            arguments=arguments or {},
            timeout=timeout,
            retry_policy=policy,
            correlation_id=correlation_id,
        )

    @mcp.tool(name="registry_invoke_stream")
    async def registry_invoke_stream(
        provider: str,
        tool: str,
        ctx: Context,
        arguments: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 3,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Invoke a tool with real-time progress notifications.

        This tool sends MCP progress notifications during execution,
        allowing the model to see progress in real-time:
        - Cold start detection
        - Provider launching
        - Tool execution
        - Retry attempts

        Args:
            provider: Provider ID or Group ID
            tool: Tool name
            ctx: MCP Context (injected automatically)
            arguments: Tool arguments (default: empty dict)
            timeout: Timeout in seconds (default: 30.0)
            max_retries: Maximum retry attempts (default: 3)
            correlation_id: Optional correlation ID for tracing (auto-generated if not provided)

        Returns:
            Tool result with _retry_metadata and _progress fields

        Note:
            Progress is sent via MCP notifications. The model receives
            updates like "cold_start: Provider is cold, launching..."
            during execution.
        """
        from ...logging_config import get_logger
        logger = get_logger(__name__)

        app_ctx = get_context()
        args = arguments or {}
        correlation_id = correlation_id or str(uuid.uuid4())

        # Get retry policy
        policy = RetryPolicy(max_attempts=max_retries)

        # Track progress events - populated synchronously
        progress_events = []
        progress_step = [0]  # Mutable counter for closure
        total_steps = 5  # Estimate: cold_start, launch, execute, process, complete
        pending_notifications = []  # Store pending async notifications

        def sync_progress_callback(stage: str, message: str, elapsed_ms: float):
            """Sync callback that collects progress and schedules MCP notification."""
            progress_step[0] += 1
            event = {
                "stage": stage,
                "message": message,
                "elapsed_ms": round(elapsed_ms, 2),
            }
            # Always append synchronously - this is the fix!
            progress_events.append(event)

            # Log to server
            logger.info(
                "operation_progress",
                provider=provider,
                tool=tool,
                correlation_id=correlation_id,
                **event,
            )

            # Schedule MCP notification (fire-and-forget)
            async def send_mcp_notification():
                try:
                    await ctx.report_progress(
                        progress=progress_step[0],
                        total=total_steps,
                        message=f"[{stage}] {message}",
                    )
                except Exception as e:
                    logger.debug("mcp_progress_notification_failed", error=str(e))

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    task = asyncio.create_task(send_mcp_notification())
                    pending_notifications.append(task)
            except Exception:
                pass  # Best effort for MCP notifications

        # Create progress tracker
        start_time = time.time()

        progress = create_progress_tracker(
            provider=provider,
            operation=tool,
            callback=sync_progress_callback,
            correlation_id=correlation_id,
        )

        # Report initial state - check cold start
        provider_obj = app_ctx.get_provider(provider) if app_ctx.provider_exists(provider) else None
        is_cold_start = provider_obj and provider_obj.state.value == "cold"

        if is_cold_start:
            progress.report(ProgressStage.COLD_START, "Provider is cold, launching...")
        else:
            progress.report(ProgressStage.READY, "Starting operation...")

        def do_invoke():
            if app_ctx.group_exists(provider):
                return _invoke_on_group(provider, tool, args, timeout, progress)

            if not app_ctx.provider_exists(provider):
                available = list(app_ctx.repository.get_all().keys())
                raise HangarProviderNotFoundError(
                    message=f"Provider '{provider}' not found",
                    provider=provider,
                    operation="invoke",
                    available_providers=available,
                )

            return _invoke_on_provider(provider, tool, args, timeout, progress)

        # Execute with retry (sync, but progress is reported async)
        result = retry_sync(
            operation=do_invoke,
            policy=policy,
            provider=provider,
            operation_name=tool,
            on_retry=lambda attempt, err, delay: progress.report(
                ProgressStage.RETRYING,
                f"Retry {attempt}/{policy.max_attempts} in {delay:.1f}s: {str(err)[:50]}",
            ),
        )

        elapsed_ms = (time.time() - start_time) * 1000

        if result.success:
            progress.complete(result.result)

            # Final progress notification
            await ctx.report_progress(
                progress=total_steps,
                total=total_steps,
                message=f"[complete] Operation completed in {elapsed_ms:.0f}ms",
            )

            response = {
                **result.result,
                "_retry_metadata": {
                    "correlation_id": correlation_id,
                    "attempts": result.attempt_count,
                    "total_time_ms": round(elapsed_ms, 2),
                    "retries": [a.error_type for a in result.attempts],
                },
                "_progress": progress_events,
            }

            # Check if result contains isError: true (provider returned error in response)
            if result.result.get("isError"):
                from ...errors import ErrorClassifier
                error_text = _extract_error_text(result.result.get("content", []))
                error_classification = ErrorClassifier.classify(Exception(error_text))

                response["_retry_metadata"]["final_error_reason"] = error_classification["final_error_reason"]
                response["_retry_metadata"]["recovery_hints"] = error_classification["recovery_hints"]

            return response
        else:
            from ...errors import ErrorClassifier

            # Classify the error for enriched metadata
            error_classification = ErrorClassifier.classify(result.final_error)

            hangar_error = map_exception_to_hangar_error(
                result.final_error,
                provider=provider,
                operation=tool,
                context={
                    "arguments": args,
                    "timeout": timeout,
                    "attempts": result.attempt_count,
                    "progress": progress_events,
                },
            )
            progress.fail(hangar_error)

            # Return error response with enriched metadata (consistent with invoke_ex)
            return {
                "content": f"Error executing tool {tool}: {str(result.final_error)}",
                "isError": True,
                "_retry_metadata": {
                    "correlation_id": correlation_id,
                    "attempts": result.attempt_count,
                    "total_time_ms": round(elapsed_ms, 2),
                    "retries": [a.error_type for a in result.attempts],
                    "final_error_reason": error_classification["final_error_reason"],
                    "recovery_hints": error_classification["recovery_hints"],
                },
                "_progress": progress_events,
            }

    @mcp.tool(name="registry_details")
    @mcp_tool_wrapper(
        tool_name="registry_details",
        rate_limit_key=lambda provider: f"registry_details:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_details(provider: str) -> dict:
        """
        Get detailed information about a provider or group.

        This is a QUERY operation - no side effects.

        Args:
            provider: Provider ID or Group ID

        Returns:
            Dictionary with full provider/group details

        Raises:
            ValueError: If provider ID is unknown or invalid
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return ctx.get_group(provider).to_status_dict()

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        query = GetProviderQuery(provider_id=provider)
        return ctx.query_bus.execute(query).to_dict()

    @mcp.tool(name="registry_warm")
    @mcp_tool_wrapper(
        tool_name="registry_warm",
        rate_limit_key=lambda providers="": "registry_warm",
        check_rate_limit=check_rate_limit,
        validate=None,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def registry_warm(providers: Optional[str] = None) -> dict:
        """
        Pre-start (warm up) providers to avoid cold start latency.

        Starts the specified providers in advance so they're ready
        when you need them. This eliminates cold start delays.

        Args:
            providers: Comma-separated list of provider IDs to warm up.
                      If empty, warms all providers.

        Returns:
            Dictionary with status for each provider:
            - warmed: List of successfully started providers
            - already_warm: List of providers that were already running
            - failed: List of providers that failed to start

        Example:
            registry_warm("math,sqlite")  # Warm specific providers
            registry_warm()               # Warm all providers
        """
        ctx = get_context()

        # Parse provider list
        if providers:
            provider_ids = [p.strip() for p in providers.split(",") if p.strip()]
        else:
            provider_ids = list(ctx.repository.get_all().keys())

        warmed = []
        already_warm = []
        failed = []

        for provider_id in provider_ids:
            # Skip groups
            if ctx.group_exists(provider_id):
                continue

            if not ctx.provider_exists(provider_id):
                failed.append({"id": provider_id, "error": "Provider not found"})
                continue

            try:
                provider_obj = ctx.get_provider(provider_id)
                if provider_obj and provider_obj.state.value == "ready":
                    already_warm.append(provider_id)
                else:
                    command = StartProviderCommand(provider_id=provider_id)
                    ctx.command_bus.send(command)
                    warmed.append(provider_id)
            except Exception as e:
                failed.append({"id": provider_id, "error": str(e)[:100]})

        return {
            "warmed": warmed,
            "already_warm": already_warm,
            "failed": failed,
            "summary": f"Warmed {len(warmed)} providers, {len(already_warm)} already warm, {len(failed)} failed",
        }

