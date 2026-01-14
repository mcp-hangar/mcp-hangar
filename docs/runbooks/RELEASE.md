# Release Operations Runbook

This runbook covers operational procedures for releasing MCP Hangar.

## Table of Contents

- [Standard Release](#standard-release)
- [Emergency Hotfix](#emergency-hotfix)
- [Release Rollback](#release-rollback)
- [Troubleshooting](#troubleshooting)

---

## Standard Release

### Prerequisites

- [ ] All CI checks passing on `main` branch
- [ ] CHANGELOG.md updated with release notes
- [ ] No blocking issues in milestone

### Procedure

#### Step 1: Verify Main Branch State

```bash
git checkout main
git pull origin main
git status  # Should be clean

# Run full test suite
uv run pytest tests/ -v
uv run pre-commit run --all-files
```

#### Step 2: Initiate Version Bump (Automated)

1. Navigate to **GitHub → Actions → Version Bump**
2. Click **Run workflow**
3. Select parameters:
   - `bump_type`: `patch` | `minor` | `major`
   - `prerelease`: empty for stable, or `rc.1` for release candidate
   - `dry_run`: `true` to preview changes first
4. Monitor workflow execution

#### Step 3: Monitor Release Pipeline

After version tag is pushed, the Release workflow triggers automatically:

1. **Validate** — Checks tag matches pyproject.toml
2. **Test** — Runs full test matrix (Python 3.11-3.14)
3. **Publish PyPI** — Builds and uploads to PyPI
4. **Publish Docker** — Builds multi-arch images, pushes to GHCR
5. **Create Release** — Creates GitHub Release with changelog

Monitor at: `https://github.com/mapyr/mcp-hangar/actions`

#### Step 4: Verify Artifacts

```bash
# Verify PyPI package
pip install mcp-hangar==<VERSION> --dry-run

# Verify Docker image
docker pull ghcr.io/mapyr/mcp-hangar:<VERSION>
docker run --rm ghcr.io/mapyr/mcp-hangar:<VERSION> --version
```

#### Step 5: Post-Release

- [ ] Verify GitHub Release page has correct changelog
- [ ] Update documentation site if needed
- [ ] Announce in relevant channels (Discord, Slack, etc.)
- [ ] Close milestone in GitHub

---

## Emergency Hotfix

Use this procedure for critical bugs in production releases.

### Severity Assessment

| Severity | Description | Response Time |
|----------|-------------|---------------|
| P0 | Security vulnerability, data loss | Immediate |
| P1 | Major functionality broken | < 4 hours |
| P2 | Significant bug, workaround exists | < 24 hours |

### Procedure

#### Step 1: Create Hotfix Branch

```bash
# From the affected version tag
git fetch --tags
git checkout -b hotfix/X.Y.Z vX.Y.Z-1  # e.g., hotfix/1.0.1 from v1.0.0
```

#### Step 2: Apply Fix

```bash
# Make minimal fix, add regression test
# ...

# Run tests
uv run pytest tests/ -v -m "not slow"
```

#### Step 3: Update Version and Changelog

```bash
# Update pyproject.toml
sed -i 's/version = ".*"/version = "X.Y.Z"/' pyproject.toml

# Add hotfix entry to CHANGELOG.md
cat >> CHANGELOG_HOTFIX.md << 'EOF'
## [X.Y.Z] - YYYY-MM-DD

### Fixed
- Description of critical fix (#issue)
EOF

# Prepend to CHANGELOG.md after header
```

#### Step 4: Tag and Push

```bash
git add -A
git commit -m "fix: [CRITICAL] description of fix"
git tag -a vX.Y.Z -m "Hotfix: description"
git push origin hotfix/X.Y.Z
git push origin vX.Y.Z
```

#### Step 5: Cherry-pick to Main

```bash
git checkout main
git cherry-pick <commit-hash>
git push origin main
```

---

## Release Rollback

If a release introduces critical issues that can't be hotfixed quickly.

### PyPI Rollback

PyPI doesn't allow re-uploading deleted versions. Instead:

1. **Yank the version** (marks as not recommended):
   ```bash
   # Via PyPI web interface or:
   pip install twine
   twine yank mcp-hangar -v X.Y.Z
   ```

2. **Release a new patch version** with the fix or revert.

### Docker Rollback

1. **Update `latest` tag** to previous stable version:
   ```bash
   docker pull ghcr.io/mapyr/mcp-hangar:X.Y.Z-1
   docker tag ghcr.io/mapyr/mcp-hangar:X.Y.Z-1 ghcr.io/mapyr/mcp-hangar:latest
   docker push ghcr.io/mapyr/mcp-hangar:latest
   ```

2. **Document the issue** in GitHub Release notes.

### Communication

- Update GitHub Release to mark as "Known Issues"
- Post in announcement channels with:
  - Affected versions
  - Impact description
  - Recommended action (upgrade to X.Y.Z+1 or pin to X.Y.Z-1)

---

## Troubleshooting

### Release Workflow Failures

#### Test Failures

```
Error: Tests failed on Python 3.X
```

**Resolution:**
1. Check test logs in Actions
2. Reproduce locally: `uv run pytest tests/ -v --tb=long`
3. Fix and push to main
4. Delete failed tag: `git push origin :refs/tags/vX.Y.Z`
5. Re-run Version Bump workflow

#### PyPI Publish Failure

```
Error: 403 Forbidden - trusted publishing not configured
```

**Resolution:**
1. Verify PyPI Trusted Publisher is configured:
   - Go to PyPI → Project → Publishing
   - Add GitHub publisher: `mapyr/mcp-hangar`, workflow `release.yml`
2. Ensure `environment: pypi` is set in workflow

#### Docker Build Failure

```
Error: buildx failed for linux/arm64
```

**Resolution:**
1. Check if Dockerfile has architecture-specific dependencies
2. May need to add platform-specific build stages
3. Consider removing arm64 from platforms temporarily

### Version Mismatch

```
Error: Version mismatch! pyproject.toml: X.Y.Z, Git tag: X.Y.W
```

**Resolution:**
1. Either update `pyproject.toml` to match tag
2. Or delete tag and re-create with correct version:
   ```bash
   git push origin :refs/tags/vX.Y.W
   git tag -d vX.Y.W
   ```

### Manual Intervention Required

If automated workflows fail and manual release is needed:

```bash
# Build package
python -m build

# Upload to PyPI (requires API token)
twine upload dist/*

# Build and push Docker
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/mapyr/mcp-hangar:X.Y.Z \
  -t ghcr.io/mapyr/mcp-hangar:latest \
  --push .
```

---

## Contacts

| Role | Contact |
|------|---------|
| Release Manager | @maintainer |
| Security Issues | [GitHub Security](https://github.com/mapyr/mcp-hangar/security) |
| Infrastructure | @infra-team |

---

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-01-12 | Initial runbook creation | CI/CD Setup |

