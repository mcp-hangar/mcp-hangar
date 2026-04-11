"""Simple math provider for testing the MCP registry."""

import os

from mcp.server.fastmcp import FastMCP

# Disable DNS rebinding protection so the provider accepts requests
# from any host within the container network (e.g. math-provider:8080).
mcp = FastMCP(
    "math-provider",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8080")),
    transport_security=None,
)


@mcp.tool(name="add")
def add(a: float, b: float) -> dict:
    """
    Add two numbers.

    Args:
        a: First number
        b: Second number

    Returns:
        Dictionary with 'result' key
    """
    return {"result": a + b}


@mcp.tool(name="subtract")
def subtract(a: float, b: float) -> dict:
    """
    Subtract b from a.

    Args:
        a: First number
        b: Number to subtract

    Returns:
        Dictionary with 'result' key
    """
    return {"result": a - b}


@mcp.tool(name="multiply")
def multiply(a: float, b: float) -> dict:
    """
    Multiply two numbers.

    Args:
        a: First number
        b: Second number

    Returns:
        Dictionary with 'result' key
    """
    return {"result": a * b}


@mcp.tool(name="divide")
def divide(a: float, b: float) -> dict:
    """
    Divide a by b.

    Args:
        a: Numerator
        b: Denominator

    Returns:
        Dictionary with 'result' key

    Raises:
        ValueError: If b is zero
    """
    if b == 0:
        raise ValueError("division by zero")
    return {"result": a / b}


@mcp.tool(name="power")
def power(base: float, exponent: float) -> dict:
    """
    Raise base to the power of exponent.

    Args:
        base: Base number
        exponent: Exponent

    Returns:
        Dictionary with 'result' key
    """
    return {"result": base**exponent}


def main():
    """Run the math provider server.

    Defaults to streamable HTTP transport on 0.0.0.0:8080.
    Override with MCP_TRANSPORT=stdio for subprocess mode.
    """
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
