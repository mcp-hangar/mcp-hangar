# MCP-Hangar Monorepo Makefile

.PHONY: all clean test lint build publish help

VERSION ?= $(shell git describe --tags --always --dirty)
PACKAGES := core operator helm-charts

##@ General

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

all: lint test build ## Run lint, test, build for all packages

lint: lint-core lint-operator lint-helm ## Lint all packages

lint-core: ## Lint Python core
	cd packages/core && ruff check mcp_hangar && ruff format --check mcp_hangar

lint-operator: ## Lint Go operator
	cd packages/operator && golangci-lint run || echo "golangci-lint not installed, skipping"

lint-helm: ## Lint Helm charts
	helm lint packages/helm-charts/mcp-hangar
	helm lint packages/helm-charts/mcp-hangar-operator

test: test-core test-operator ## Run tests for all packages

test-core: ## Test Python core
	cd packages/core && pytest

test-operator: ## Test Go operator
	cd packages/operator && make test || echo "Go tests skipped"

build: build-core build-operator build-helm ## Build all packages

build-core: ## Build Python package
	cd packages/core && pip install hatch && hatch build

build-operator: ## Build Go binary
	cd packages/operator && make build || echo "Go build skipped"

build-helm: ## Package Helm charts
	@mkdir -p dist
	helm package packages/helm-charts/mcp-hangar -d dist/
	helm package packages/helm-charts/mcp-hangar-operator -d dist/

##@ Docker

docker: docker-core docker-operator ## Build all Docker images

docker-core: ## Build core Docker image
	docker build -t ghcr.io/mapyr/mcp-hangar:$(VERSION) packages/core

docker-operator: ## Build operator Docker image
	docker build -t ghcr.io/mapyr/mcp-hangar-operator:$(VERSION) packages/operator

docker-push: ## Push all Docker images
	docker push ghcr.io/mapyr/mcp-hangar:$(VERSION)
	docker push ghcr.io/mapyr/mcp-hangar-operator:$(VERSION)

##@ Release

publish: publish-core publish-operator publish-helm ## Publish all packages

publish-core: ## Publish to PyPI
	cd packages/core && hatch publish

publish-operator: docker-operator ## Push operator image
	docker push ghcr.io/mapyr/mcp-hangar-operator:$(VERSION)
	docker tag ghcr.io/mapyr/mcp-hangar-operator:$(VERSION) ghcr.io/mapyr/mcp-hangar-operator:latest
	docker push ghcr.io/mapyr/mcp-hangar-operator:latest

publish-helm: build-helm ## Push Helm charts to OCI
	helm push dist/mcp-hangar-*.tgz oci://ghcr.io/mapyr/charts
	helm push dist/mcp-hangar-operator-*.tgz oci://ghcr.io/mapyr/charts

release: ## Create a release (use VERSION=x.y.z)
	@if [ -z "$(VERSION)" ]; then echo "VERSION required"; exit 1; fi
	git tag -a v$(VERSION) -m "Release v$(VERSION)"
	git push origin v$(VERSION)

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

##@ Cleanup

clean: ## Clean build artifacts
	rm -rf dist/
	rm -rf packages/core/dist/
	rm -rf packages/operator/bin/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

##@ Development Setup

setup: setup-core setup-operator ## Setup development environment

setup-core: ## Setup Python development environment
	cd packages/core && pip install -e ".[dev]"

setup-operator: ## Setup Go development environment
	cd packages/operator && go mod download
