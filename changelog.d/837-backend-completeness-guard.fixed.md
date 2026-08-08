**core:** the persistence-backend completeness guard now invokes each concern
instead of only checking that the method exists. A third-party backend whose
concern method is callable but returns `None` -- exactly how the tool-access
policy store was once silently disabled -- passed the guard, because it only
tested callability. `create_backend` now calls each concern and treats a `None`
return as a missing concern, so an incomplete backend is refused as the
docstrings promise. Built-in backends cache their adapters, so the extra call is
free.
