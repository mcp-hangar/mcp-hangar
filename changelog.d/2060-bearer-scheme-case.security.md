**security:** a JWT sent as `Authorization: bearer <token>` (or any other casing of
the scheme) now authenticates. The scheme is case-insensitive, and the
`?token=` path already treated it so, but the header path compared it with
`Bearer` exactly and treated anything else as no credentials -- a 401, or, where
anonymous access is allowed, a valid caller served as anonymous
