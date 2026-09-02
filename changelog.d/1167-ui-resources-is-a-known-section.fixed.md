**core:** `HANGAR_CONFIG_STRICT=1` refused to start a gateway whose config
declared `ui_resources:`. The block has been read since 2.13.1 and is
documented as the way to enable `ui://` resources, but it was missing from the
config schema's section table, so `validate_config` reported it as a key
nothing reads -- fatal under the strict posture the docs recommend for CI and
staging, and a misleading "the setting simply does not apply" warning
everywhere else. A test now asserts that every top-level section the loader
reads is a section the schema knows
