**core:** a discovered server that was still registered but no longer tracked
by discovery, for example because its deregistration did not apply, could come
back under the same name with a different definition. Discovery then recorded
the new definition's fingerprint but kept running the old one: a remote server
stayed at its old endpoint and address, a command or image stayed the old one,
and no later cycle corrected it. The reported definition now replaces the
running one, as a change to a tracked server does. A change that is refused
leaves the running server and its recorded fingerprint as they were, and the
server stays tracked, so it is still removed once its source stops reporting it.
