**core:** the project page on PyPI told every visitor `pip install mcp-hangar`
resolves to **2.0.0**. The README is baked into the distribution at build time,
so correcting it in the repository does not correct what PyPI renders -- only a
new release does. This one carries the corrected Install section, which no
longer names a version at all: migration steps live in the upgrade guide, and
the tested core / operator / chart combinations in the compatibility matrix.
