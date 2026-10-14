**security:** every state change of an approval request is now a conditional
update from `pending` with a row-count check, in both the SQLite and the
PostgreSQL repository. A resolve on one replica, an expiry on another and a
cancel from a batch deadline used to overwrite each other, so the record and the
event could disagree with what the held call did. Now one of them wins and the
rest are told so: a lost resolve answers `409` with the winning `state`, and a
decision that lands as the hold's deadline passes is honoured instead of being
overwritten as `expired`. Held calls publish exactly one terminal event
