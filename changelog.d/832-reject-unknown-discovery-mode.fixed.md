**core:** discovery source configuration now refuses an unknown `mode` instead
of silently treating it as `additive`. Correct misspelled values to
`additive` or `authoritative` before upgrading.
