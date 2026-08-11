**core:** the upgrade note for 2.6.0. Two changes in that release can stop a
deployment that works today -- a gateway with per-tenant digest pins and
authentication off no longer starts, and the `hangar_*` tools now require the
permission their REST equivalent has always required -- and both needed the
before/after and the remedy written down rather than inferred from a changelog
entry. Includes the tool-to-permission table with the built-in roles that hold
each one, because the two combinations that surprise people (`provider-admin`
cannot run lifecycle, `developer` cannot approve or read metrics) are not
guessable from the role names
