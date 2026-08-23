**core:** a call routed to a group is checked against the withdrawal gate and its
digest pin. Both looked the tool up under the group id while the projection
registry is keyed by the member that started, so the lookup returned `None` --
"unknown tool, do not block" -- and a pinned tool served through a group was never
validated against its pin, in either topology and with no listing filter behind
it. The group id is asked first, the selected member answers otherwise
