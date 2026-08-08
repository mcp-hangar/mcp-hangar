**core:** the "certificate verification is off for this upstream" startup
warning no longer fires when `tls.verify_ssl: false` is set alongside a
`tls.ca_cert_path`. The CA path wins in the client -- verification is enforced
against that CA -- so the old warning contradicted the actual behaviour and sent
an operator debugging a failed handshake toward the very setting doing the
enforcing. That combination now logs an accurate message saying verification is
enforced against the configured CA; the "verification is off" warning fires only
when verification is genuinely off.
