**core:** registering or updating a `remote` server over REST with an endpoint
the HTTP launcher cannot dial -- a scheme other than http or https, or no host,
such as `file:///etc/passwd` -- now answers 422. It was accepted, stored and
listed, and failed at every start
