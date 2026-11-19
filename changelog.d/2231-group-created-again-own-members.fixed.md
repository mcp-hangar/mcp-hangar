**core:** a group created again over a row a configuration file had claimed
took, at this replica's next restart, every member added over REST to the
earlier group of that id, so it routed to servers its new creator never added,
and a reload and a restart disagreed about who was in it. Each creation is now
a generation of the group's row, and a member row records the generation it
was added to: a group restored from its row takes only its own members, while a
peer whose file still declares the group keeps them all, as before
