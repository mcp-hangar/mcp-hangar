**ci:** a release PR could be left unmergeable by its own workflow. When the
changelog assembly fell back to `GITHUB_TOKEN` -- a push with which does not
trigger workflows -- the commit it pushed became the PR head with no runs at
all, so all 13 required checks stayed "expected" and the merge was refused even
with `--admin`, a ruleset requirement being nothing a bypass clears. The
existing warning was guarded on the release app being *unconfigured*, so the
case that actually happened (app configured, token step yielded nothing) said
nothing at all. The fallback is now detected by comparing the tokens, the
assembly re-fires the checks itself, and a missing app token is reported where
it is caused
