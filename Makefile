# MCP-Hangar Makefile

.PHONY: all clean test lint build publish help dev dev-backend

VERSION ?= $(shell git describe --tags --always --dirty)

##@ General

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# A config file to start with; unset, `mcp-hangar` looks where it always does (see `mcp-hangar --help`).
CONFIG ?=

##@ Development

dev: ## Start backend HTTP server
	mcp-hangar $(if $(CONFIG),--config $(CONFIG)) serve --http

dev-backend: ## Start backend HTTP server only
	mcp-hangar $(if $(CONFIG),--config $(CONFIG)) serve --http


all: lint test build ## Run lint, test, build

lint: lint-core ## Lint all

lint-core: ## Lint Python core
	ruff check src/mcp_hangar && ruff format --check src/mcp_hangar

test: test-core ## Run tests

test-core: ## Test Python core
	pytest

build: build-core ## Build

build-core: ## Build Python package
	pip install hatch && hatch build

##@ Docker

docker: docker-core ## Build Docker image

docker-core: ## Build core Docker image
	docker build -t ghcr.io/mcp-hangar/mcp-hangar:$(VERSION) .

docker-push: ## Push Docker image
	docker push ghcr.io/mcp-hangar/mcp-hangar:$(VERSION)

##@ Release

publish: publish-core ## Publish package

publish-core: ## Publish to PyPI
	hatch publish

release: ## Create a release (use VERSION=x.y.z)
	@if [ -z "$(VERSION)" ]; then echo "VERSION required"; exit 1; fi
	git tag -a v$(VERSION) -m "Release v$(VERSION)"
	git push origin v$(VERSION)

changelog-check: ## Validate pending changelog fragments in changelog.d/
	python scripts/build_changelog.py check

changelog-preview: ## Render the next release section (use VERSION=x.y.z)
	@if [ -z "$(VERSION)" ]; then echo "VERSION required"; exit 1; fi
	python scripts/build_changelog.py preview --version $(VERSION)

##@ Quick Start

quickstart: ## Run quickstart demo
	cd examples/quickstart && docker compose up -d
	@echo ""
	@echo "MCP-Hangar running at http://localhost:8080"
	@echo "Grafana at http://localhost:3000"

quickstart-down: ## Stop quickstart demo
	cd examples/quickstart && docker compose down

##@ Cleanup

clean: ## Clean build artifacts
	rm -rf dist/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

##@ Development Setup

setup: setup-core ## Setup development environment

setup-core: ## Setup Python development environment
	pip install -e ".[dev]"
