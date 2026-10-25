**security:** the SSRF policy's cloud-metadata hostname rule now also refuses the
name written with a trailing dot. `metadata.google.internal.` is the same name
fully qualified, and it passed the name check; the address check behind it still
refused the metadata IP, so this closes a defence-in-depth gap
