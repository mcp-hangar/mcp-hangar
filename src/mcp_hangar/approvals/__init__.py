"""Approval gate module.

Provides human-in-the-loop approval for tool invocations matching
configured approval_list patterns. A held tool waits for a decision taken
over the REST API; the request reaches a human on the domain event stream
(``/api/ws/events``, the ``event_stream`` channel) or through a vendor
adapter installed out of tree (see ADR-016).
"""
