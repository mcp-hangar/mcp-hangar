**deps:** the `full` extra now requires fpdf2 2.8.8 or later, the first release
that fixes CVE-2026-114609 (implicit image dereference) and CVE-2026-114610 (SVG
`<use>` expansion causing unbounded memory growth). The lockfile already resolved
2.8.9. The old floor of 2.8.0 let an existing install keep a vulnerable version
across an upgrade.
