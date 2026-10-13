**core:** CEF and LEEF records name the running release as the device version
instead of the `0.15.0` they carried since that release, and a CEF state-change
record carries its transition (`msg=cold -> initializing`). Two transitions of
one server used to be byte-identical CEF lines
