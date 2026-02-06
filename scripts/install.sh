#!/usr/bin/env bash
#
# MCP Hangar Installer
# https://get.mcp-hangar.io
#
# Usage:
#   curl -sSL https://get.mcp-hangar.io | bash
#
# This script:
#   1. Detects available Python package manager (uv, pip)
#   2. Installs mcp-hangar package
#   3. Verifies installation
#
# Requirements:
#   - Python 3.11+
#   - pip or uv
#
# Environment variables:
#   MCP_HANGAR_VERSION  - Specific version to install (default: latest)
#   MCP_HANGAR_QUIET    - Suppress output (default: false)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
VERSION="${MCP_HANGAR_VERSION:-}"
QUIET="${MCP_HANGAR_QUIET:-false}"
PACKAGE_NAME="mcp-hangar"

# Logging functions
log() {
    if [[ "$QUIET" != "true" ]]; then
        echo -e "$1"
    fi
}

log_info() {
    log "${BLUE}==>${NC} $1"
}

log_success() {
    log "${GREEN}==>${NC} $1"
}

log_warning() {
    log "${YELLOW}Warning:${NC} $1"
}

log_error() {
    echo -e "${RED}Error:${NC} $1" >&2
}

# Check Python version
check_python() {
    local python_cmd=""

    # Try python3 first, then python
    if command -v python3 &>/dev/null; then
        python_cmd="python3"
    elif command -v python &>/dev/null; then
        python_cmd="python"
    else
        log_error "Python not found. Please install Python 3.11 or later."
        exit 1
    fi

    # Check version
    local version
    version=$($python_cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local major minor
    IFS='.' read -r major minor <<< "$version"

    if [[ "$major" -lt 3 ]] || [[ "$major" -eq 3 && "$minor" -lt 11 ]]; then
        log_error "Python 3.11+ required. Found: Python $version"
        exit 1
    fi

    log_info "Found Python $version"
    echo "$python_cmd"
}

# Detect package manager (prefer uv over pip)
detect_package_manager() {
    if command -v uv &>/dev/null; then
        log_info "Using uv for installation"
        echo "uv"
    elif command -v pip3 &>/dev/null; then
        log_info "Using pip3 for installation"
        echo "pip3"
    elif command -v pip &>/dev/null; then
        log_info "Using pip for installation"
        echo "pip"
    else
        log_error "No package manager found. Please install uv or pip."
        log_info "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

# Install package
install_package() {
    local pkg_mgr="$1"
    local package="$PACKAGE_NAME"

    if [[ -n "$VERSION" ]]; then
        package="${PACKAGE_NAME}==${VERSION}"
    fi

    log_info "Installing $package..."

    case "$pkg_mgr" in
        uv)
            if [[ -n "$VERSION" ]]; then
                uv pip install "$PACKAGE_NAME==$VERSION" --quiet
            else
                uv pip install "$PACKAGE_NAME" --quiet
            fi
            ;;
        pip|pip3)
            if [[ -n "$VERSION" ]]; then
                $pkg_mgr install "$PACKAGE_NAME==$VERSION" --quiet
            else
                $pkg_mgr install "$PACKAGE_NAME" --quiet
            fi
            ;;
    esac
}

# Verify installation
verify_installation() {
    if command -v mcp-hangar &>/dev/null; then
        local installed_version
        installed_version=$(mcp-hangar --version 2>/dev/null || echo "unknown")
        log_success "MCP Hangar installed successfully!"
        log_info "Version: $installed_version"
        return 0
    else
        log_error "Installation verification failed. 'mcp-hangar' command not found."
        log_info "You may need to add the installation directory to your PATH."
        return 1
    fi
}

# Print next steps
print_next_steps() {
    log ""
    log "${GREEN}Installation complete!${NC}"
    log ""
    log "Next steps:"
    log "  ${BLUE}1.${NC} Initialize configuration:"
    log "     mcp-hangar init"
    log ""
    log "  ${BLUE}2.${NC} Start the server:"
    log "     mcp-hangar serve"
    log ""
    log "Or run everything in one command:"
    log "  ${BLUE}mcp-hangar init -y && mcp-hangar serve${NC}"
    log ""
    log "Documentation: https://mcp-hangar.io"
}

# Main
main() {
    log ""
    log "${BLUE}MCP Hangar Installer${NC}"
    log ""

    # Check Python
    local python_cmd
    python_cmd=$(check_python)

    # Detect package manager
    local pkg_mgr
    pkg_mgr=$(detect_package_manager)

    # Install
    install_package "$pkg_mgr"

    # Verify
    if verify_installation; then
        print_next_steps
    else
        exit 1
    fi
}

main "$@"
