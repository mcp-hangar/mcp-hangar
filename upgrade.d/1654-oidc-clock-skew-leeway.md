### `oidc.clock_skew_leeway_seconds` now takes effect, and an invalid value refuses the configuration

The key was ignored: the leeway was always 60 seconds. If your configuration sets it (top level or on an `oidc.issuers` entry), that value now applies, so a tighter value starts rejecting tokens that are further out of step with the issuer's clock than before. A value that is not a whole number from 0 to 300 (including a quoted number) now stops the gateway at boot or the reload with a message naming the key, where it was silently ignored. Remove the key to keep the default of 60.
