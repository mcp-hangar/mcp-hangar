"""Simple math provider for testing the MCP registry."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("math-provider")


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
    """Run the math provider server."""
    mcp.run()


if __name__ == "__main__":
    main()
