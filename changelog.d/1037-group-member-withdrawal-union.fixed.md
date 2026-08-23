**core:** a prompt or resource withdrawn on a group MEMBER is now hidden for the
whole group. The prompts and resources surfaces ask about a group under its group
id, and the withdrawal overlay is keyed by the id it was declared under, so a
member's `withdrawn_prompts` / `withdrawn_resources` was invisible to them. The
union is fail-closed: members of one group are interchangeable, so an item
withdrawn on one of two identical backends is not a state an operator can have
meant
