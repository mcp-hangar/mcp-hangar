**core:** a remote server whose `tls.verify_ssl` is not a boolean -- a quoted
`"false"` is the usual case -- now fails to start with an error naming
`tls.verify_ssl`. httpx read the string as a CA bundle path, so every start
failed with `No such file or directory` and the `tls_verification_disabled`
warning never appeared
