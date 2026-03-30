# MCP-Hangar Makefile

.PHONY: all clean test lint build publish help dev dev-backend

VERSION ?= $(shell git describe --tags --always --dirty)

##@ General

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

CONFIG ?= config.max.yaml

##@ Development

dev: ## Start backend HTTP server
	mcp-hangar --config $(CONFIG) serve --http

dev-backend: ## Start backend HTTP server only
	mcp-hangar --config $(CONFIG) serve --http


all: lint test build ## Run lint, test, build

lint: lint-core proto-lint ## Lint all

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

##@ CI

check-boundary: ## Check enterprise import boundary
	bash scripts/check_enterprise_boundary.sh

##@ Documentation

docs: ## Build documentation
	mkdocs build

docs-serve: ## Serve documentation locally
	mkdocs serve

##@ Quick Start

quickstart: ## Run quickstart demo
	cd examples/quickstart && docker compose up -d
	@echo ""
	@echo "MCP-Hangar running at http://localhost:8080"
	@echo "Grafana at http://localhost:3000"

quickstart-down: ## Stop quickstart demo
	cd examples/quickstart && docker compose down

##@ Proto / API

proto-gen: ## Generate Go code from proto definitions
	@command -v buf >/dev/null 2>&1 || { echo "buf CLI required: https://buf.build/docs/installation"; exit 1; }
	cd api && buf generate
	@echo "Proto code generated in api/gen/go/"

proto-lint: ## Lint proto definitions
	@command -v buf >/dev/null 2>&1 || { echo "buf CLI required: https://buf.build/docs/installation"; exit 1; }
	cd api && buf lint

proto-breaking: ## Check proto breaking changes against main
	@command -v buf >/dev/null 2>&1 || { echo "buf CLI required: https://buf.build/docs/installation"; exit 1; }
	cd api && buf breaking --against ".git#branch=main,subdir=api/proto"

##@ Multi-Repo Workspace

workspace-setup: ## One-time multi-repo workspace setup
	./scripts/dev-workspace.sh setup

workspace-start: ## Start dev services (MODE=oss|managed|full)
	./scripts/dev-workspace.sh start $(MODE)

workspace-stop: ## Stop all dev services
	./scripts/dev-workspace.sh stop

workspace-status: ## Show dev workspace status
	./scripts/dev-workspace.sh status

##@ Cleanup

clean: ## Clean build artifacts
	rm -rf dist/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

##@ Development Setup

setup: setup-core setup-proto ## Setup development environment

setup-core: ## Setup Python development environment
	pip install -e ".[dev]"

setup-proto: ## Setup proto tooling and generate code
	@command -v buf >/dev/null 2>&1 && { cd api && buf generate; echo "Proto code generated."; } || echo "buf CLI not installed, skipping proto setup"

