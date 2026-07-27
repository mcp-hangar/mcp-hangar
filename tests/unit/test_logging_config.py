def test_no_log_reaches_stdout_before_setup_logging():
    """Importing the package must not leave stdout as the default log stream (#563).

    structlog's out-of-the-box factory prints to **stdout**. On the stdio
    transport stdout is the JSON-RPC stream, so a log emitted before
    `setup_logging()` runs — a module-import-time log, for instance — corrupts
    the session and the client dies on a parse error.

    Checked in a subprocess on purpose: the assertion is about the state right
    after import, and any earlier test that called `setup_logging()` would have
    replaced the global config, making an in-process check pass or fail on test
    order rather than on behaviour.
    """
    import subprocess
    import sys

    program = (
        "import sys;"
        "from mcp_hangar.logging_config import get_logger;"
        # Exactly the shape that leaked: a log before anyone configured logging.
        "get_logger('probe').debug('EARLY-LINE');"
        "print('SENTINEL-STDOUT-IS-OURS')"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "SENTINEL-STDOUT-IS-OURS", (
        f"something logged to stdout before setup_logging: {result.stdout!r}"
    )
    assert "EARLY-LINE" in result.stderr, "the early log vanished instead of moving to stderr"
