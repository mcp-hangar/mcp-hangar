**core:** on a `front_door` gateway, a server a configuration reload adds is now
started as boot starts every configured server, so its tools are listed and
callable, and each client whose listing that changes, over a `GET /mcp` stream
or a stdio session, is sent `notifications/tools/list_changed`. A reload used to commit the added
server cold and start nothing, so its tools were never listed and a call to one
answered `-32601` until the gateway restarted. A server the reload rebuilds is
started again too, so the front door lists its new catalogue rather than the one
of the process the reload stopped. In `egress` a reload still starts nothing
