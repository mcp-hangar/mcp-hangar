**security:** the connect-time SSRF guard no longer dials a remote server by name
when its own DNS lookup fails. It used to pass the request on with the hostname
intact, so httpcore resolved the name a second time with no check, and a
resolver that failed the first lookup and answered the second with a private or
metadata address got a connection the guard never judged. The request now fails
with a connection error. Servers without SSRF enforcement, and names that
resolve, are unaffected
