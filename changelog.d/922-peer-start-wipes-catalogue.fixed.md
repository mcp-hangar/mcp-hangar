A replica no longer loses tools when a peer restarts a server. The tool-catalogue
handler was classified as a projection, so it ran on peers' `McpServerStarted`
events -- but it rebuilds from the local aggregate rather than from the event
(which carries `tools_count`, not schemas), and the rebuild is a replace. A
follower whose own copy of that server was cold therefore rebuilt it from nothing
and deleted a catalogue it was correctly serving. It is now `HandlerKind.LOCAL_VIEW`,
a third kind for handlers that read local state and so, like effects, must run only
on the instance that produced the event. (#922)
