**security:** a tool whose definition drifted from its digest pin is now withheld
from every listing a model reads, not only refused when called. After an upstream
rewrote a pinned tool's description, `tools/call` was refused, but `tools/list`
through Hangar still returned the new description, so the rug pull's instruction
reached the model on every listing. The tool is now absent from the front door's
`tools/list` (stdio and streamable HTTP) and from `hangar_tools`, and its call is
answered like a call of a tool that does not exist. `notifications/tools/list_changed`
is sent when it is withheld and again when it returns. `hangar_details` lists it
under `withheld_tools` with the reason `digest_drift` and both digests, without
the description, and the REST tool routes mark it `withheld: "digest_drift"`. Only
a pin in `block` mode withholds: under `warn` and `audit` the call goes through,
so the tool stays listed. The interval re-check withholds as well as refuses.
A caller is told nothing about a tool its policy hides
