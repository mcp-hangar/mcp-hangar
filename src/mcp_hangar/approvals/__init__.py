"""Approval gate module.

Provides human-in-the-loop approval for tool invocations matching
configured approval_list patterns. Tools are held pending a human
decision via the dashboard, an MCP prompt channel, or a vendor adapter
installed out of tree (see ADR-016).
"""
