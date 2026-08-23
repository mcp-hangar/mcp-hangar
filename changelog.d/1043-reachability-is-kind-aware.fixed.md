**core:** the startup reachability check no longer demands an approval gate for a
policy no gate can serve. It read `approval_list` off every registered policy,
including the prompt and resource kinds added in 2.13.0, and refused the boot over
it -- so one configuration was fail-open at request time and fail-closed at boot
at the same time. `iter_registered_policies()` takes a `kind` filter and both the
gate and the delivery-channel checks ask for tools
