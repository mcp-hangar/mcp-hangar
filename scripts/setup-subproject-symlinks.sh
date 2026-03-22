#!/bin/bash
set -e
cd "$(dirname "$0")/.."

for pkg in core operator ui helm-charts; do
  PKG_DIR="packages/$pkg"
  ROOT_CLAUDE=".claude"
  ROOT_OPENCODE=".opencode"

  # Create directory structure
  mkdir -p "$PKG_DIR/.claude/agents"
  mkdir -p "$PKG_DIR/.claude/commands/gsd"
  mkdir -p "$PKG_DIR/.claude/hooks"
  mkdir -p "$PKG_DIR/.claude/get-shit-done"
  mkdir -p "$PKG_DIR/.opencode/agents"
  mkdir -p "$PKG_DIR/.opencode/command"
  mkdir -p "$PKG_DIR/.opencode/hooks"
  mkdir -p "$PKG_DIR/.opencode/get-shit-done"

  # Symlink .planning so GSD state is accessible from subproject sessions
  ln -sfn "../../.planning" "$PKG_DIR/.planning"

  # Symlink GSD framework bins/templates/workflows/references for .claude
  for item in bin templates workflows references VERSION; do
    if [ -e "$ROOT_CLAUDE/get-shit-done/$item" ]; then
      ln -sfn "../../../../$ROOT_CLAUDE/get-shit-done/$item" "$PKG_DIR/.claude/get-shit-done/$item"
    fi
  done

  # Symlink GSD agents for .claude
  for agent in $ROOT_CLAUDE/agents/gsd-*.md; do
    if [ -f "$agent" ]; then
      BASENAME=$(basename "$agent")
      ln -sfn "../../../../$agent" "$PKG_DIR/.claude/agents/$BASENAME"
    fi
  done

  # Symlink GSD commands for .claude
  for cmd in $ROOT_CLAUDE/commands/gsd/*.md; do
    if [ -f "$cmd" ]; then
      BASENAME=$(basename "$cmd")
      ln -sfn "../../../../../$cmd" "$PKG_DIR/.claude/commands/gsd/$BASENAME"
    fi
  done

  # Symlink hooks for .claude
  for hook in $ROOT_CLAUDE/hooks/*.js; do
    if [ -f "$hook" ]; then
      BASENAME=$(basename "$hook")
      ln -sfn "../../../../$hook" "$PKG_DIR/.claude/hooks/$BASENAME"
    fi
  done

  # Symlink gsd-file-manifest.json for .claude
  if [ -f "$ROOT_CLAUDE/gsd-file-manifest.json" ]; then
    ln -sfn "../../../$ROOT_CLAUDE/gsd-file-manifest.json" "$PKG_DIR/.claude/gsd-file-manifest.json"
  fi

  # Symlink GSD framework bins/templates/workflows/references for .opencode
  for item in bin templates workflows references VERSION; do
    if [ -e "$ROOT_OPENCODE/get-shit-done/$item" ]; then
      ln -sfn "../../../../$ROOT_OPENCODE/get-shit-done/$item" "$PKG_DIR/.opencode/get-shit-done/$item"
    fi
  done

  # Symlink GSD agents for .opencode
  for agent in $ROOT_OPENCODE/agents/gsd-*.md; do
    if [ -f "$agent" ]; then
      BASENAME=$(basename "$agent")
      ln -sfn "../../../../$agent" "$PKG_DIR/.opencode/agents/$BASENAME"
    fi
  done

  # Symlink GSD commands for .opencode (flat structure, no gsd/ subdirectory)
  for cmd in $ROOT_OPENCODE/command/gsd-*.md; do
    if [ -f "$cmd" ]; then
      BASENAME=$(basename "$cmd")
      ln -sfn "../../../../$cmd" "$PKG_DIR/.opencode/command/$BASENAME"
    fi
  done

  # Symlink hooks for .opencode
  for hook in $ROOT_OPENCODE/hooks/*; do
    if [ -f "$hook" ]; then
      BASENAME=$(basename "$hook")
      ln -sfn "../../../../$hook" "$PKG_DIR/.opencode/hooks/$BASENAME"
    fi
  done

  # Symlink gsd-file-manifest.json for .opencode
  if [ -f "$ROOT_OPENCODE/gsd-file-manifest.json" ]; then
    ln -sfn "../../../$ROOT_OPENCODE/gsd-file-manifest.json" "$PKG_DIR/.opencode/gsd-file-manifest.json"
  fi

  echo "Symlinks for $pkg: done"
done

echo "All symlinks created successfully"
