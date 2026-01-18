# Changelog

All notable changes to this package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-01-18

### Added
- Monorepo structure with packages/core for Python code
- CQRS + Event Sourcing architecture
- Provider state machine with COLD → INITIALIZING → READY → DEGRADED → DEAD transitions
- Health monitoring with circuit breakers
- Prometheus metrics at /metrics
- Structured JSON logging via structlog

### Changed
- Restructured from flat layout to packages/core/

## [0.1.0] - 2025-XX-XX

### Added
- Initial release
- Basic provider management
- MCP protocol support
