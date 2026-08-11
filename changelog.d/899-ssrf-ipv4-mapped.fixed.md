**core:** the SSRF denylist accepted IPv4-mapped IPv6 forms of addresses it
refused in ordinary form. `::ffff:169.254.169.254` and `::ffff:127.0.0.1`
passed both the floor and the human private-range checks because membership of
an `IPv6Address` in an IPv4 network is always false. `_in_any` now normalizes
mapped addresses before the check, so mapped and unmapped forms of the same
host get the same answer at registration and at connect-time pinning
