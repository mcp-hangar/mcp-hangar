**core:** a group behind `tool_access.mode: front_door` collided with itself
and served none of its tools. The flat projection keyed on the bare tool name
and dropped both entries on a collision -- and group members expose the same
names by definition. Members of one group now collapse into a single logical
server: the projection lists each shared tool once, policy is checked against
the group (the same check the call path applies), and calls dispatch through
the group id so member selection stays with the group's strategy (round-robin,
canary, health). Collisions across *different* backends are still dropped
