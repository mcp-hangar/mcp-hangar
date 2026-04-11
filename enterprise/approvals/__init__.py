"""Enterprise approval gate module (BSL 1.1).

Provides human-in-the-loop approval for tool invocations matching
configured approval_list patterns. Tools are held pending a human
decision via dashboard, Slack, or MCP prompt channel.
"""
