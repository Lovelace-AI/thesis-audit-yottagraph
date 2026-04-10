#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PKG_DIR="$ROOT_DIR/packages/aether-instructions"

# --- Parse arguments ---------------------------------------------------------

BUMP=""
PUBLISH=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bump)    BUMP="$2"; shift 2 ;;
    --publish) PUBLISH=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--bump patch|minor|major] [--publish] [--dry-run]

Bundles aether-dev source rules, commands, and skills into the
@yottagraph-app/aether-instructions npm package (original filenames).

Options:
  --bump <level>   Bump package version (patch, minor, major)
  --publish        Publish to npmjs after bundling
  --dry-run        Show what would happen without writing files
  -h, --help       Show this help
EOF
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# --- Validate sources --------------------------------------------------------

if [[ ! -d "$ROOT_DIR/rules" ]]; then
  echo "Error: rules/ directory not found in $ROOT_DIR" >&2
  exit 1
fi

# --- Clean package directories -----------------------------------------------

echo "==> Cleaning package directories"

for subdir in rules commands skills; do
  target="$PKG_DIR/$subdir"
  if $DRY_RUN; then
    echo "    Would clean $target/"
  else
    rm -rf "$target"
    mkdir -p "$target"
  fi
done

# --- Copy rules (original names) --------------------------------------------

echo "==> Bundling rules"
rules_count=0
for f in "$ROOT_DIR"/rules/*.mdc; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f")"
  if $DRY_RUN; then
    echo "    $base"
  else
    cp "$f" "$PKG_DIR/rules/$base"
  fi
  rules_count=$((rules_count + 1))
done
echo "    $rules_count rules"

# --- Copy commands (original names, minus exclusions) ------------------------

echo "==> Bundling commands"
commands_count=0
for f in "$ROOT_DIR"/commands/*.md; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f")"
  if $DRY_RUN; then
    echo "    $base"
  else
    cp "$f" "$PKG_DIR/commands/$base"
  fi
  commands_count=$((commands_count + 1))
done
echo "    $commands_count commands"

# --- Copy skills (original names) -------------------------------------------

echo "==> Bundling skills"
skills_count=0
if [[ -d "$ROOT_DIR/skills" ]]; then
  for d in "$ROOT_DIR"/skills/*/; do
    [[ -d "$d" ]] || continue
    base="$(basename "$d")"
    if $DRY_RUN; then
      echo "    $base/"
    else
      cp -r "$d" "$PKG_DIR/skills/$base"
    fi
    skills_count=$((skills_count + 1))
  done
fi
echo "    $skills_count skill directories"

# --- Bundle data-mode variants (e.g. mcp-only) -------------------------------

echo "==> Bundling variants/mcp-only"
VARIANT_BASE="$PKG_DIR/variants/mcp-only"
if $DRY_RUN; then
  echo "    Would clean $PKG_DIR/variants/ and copy rules-mcp-only/, commands-mcp-only/, skills-mcp-only/"
else
  rm -rf "$PKG_DIR/variants"
  mkdir -p "$VARIANT_BASE/rules" "$VARIANT_BASE/commands" "$VARIANT_BASE/skills"
  if [[ -d "$ROOT_DIR/rules-mcp-only" ]]; then
    for f in "$ROOT_DIR/rules-mcp-only"/*.mdc; do
      [[ -f "$f" ]] || continue
      cp "$f" "$VARIANT_BASE/rules/"
    done
  fi
  if [[ -d "$ROOT_DIR/commands-mcp-only" ]]; then
    for f in "$ROOT_DIR/commands-mcp-only"/*.md; do
      [[ -f "$f" ]] || continue
      cp "$f" "$VARIANT_BASE/commands/"
    done
  fi
  if [[ -d "$ROOT_DIR/skills-mcp-only" ]]; then
    for d in "$ROOT_DIR/skills-mcp-only"/*/; do
      [[ -d "$d" ]] || continue
      base="$(basename "$d")"
      cp -r "$d" "$VARIANT_BASE/skills/$base"
    done
  fi
fi

# --- Bump version ------------------------------------------------------------

if [[ -n "$BUMP" ]]; then
  echo "==> Bumping version ($BUMP)"
  if $DRY_RUN; then
    echo "    Would run: npm version $BUMP (in $PKG_DIR)"
  else
    cd "$PKG_DIR"
    npm version "$BUMP" --no-git-tag-version
    NEW_VERSION="$(node -p "require('./package.json').version")"
    echo "    New version: $NEW_VERSION"
    cd "$ROOT_DIR"
  fi
else
  NEW_VERSION="$(node -p "require('$PKG_DIR/package.json').version")"
fi

# --- Publish -----------------------------------------------------------------

if $PUBLISH; then
  echo "==> Publishing to npmjs"
  if $DRY_RUN; then
    echo "    Would run: npm publish (in $PKG_DIR)"
  else
    cd "$PKG_DIR"
    npm publish --access public
    echo "    Published @yottagraph-app/aether-instructions@${NEW_VERSION}"
    cd "$ROOT_DIR"
  fi
fi

# --- Summary -----------------------------------------------------------------

echo ""
echo "Done! Package contents:"
echo "  Rules:    $rules_count files"
echo "  Commands: $commands_count files"
echo "  Skills:   $skills_count directories"
echo "  Version:  ${NEW_VERSION:-$(node -p "require('$PKG_DIR/package.json').version")}"

if ! $PUBLISH; then
  echo ""
  echo "To publish: $(basename "$0") --publish"
  echo "Or: cd packages/aether-instructions && npm publish --access public"
fi
