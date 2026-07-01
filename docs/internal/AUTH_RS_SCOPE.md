# Resource-Server Scope: RFC 9207 and CIMD Non-Applicability

## Purpose

This document clarifies what Hangar's OAuth Resource Server does and does not do in relation to recent authentication SEPs (Standardization Enhancement Proposals), specifically addressing RFC 9207 (issuer identification) and CIMD (Client ID Metadata Documents).

## RFC 9207: Issuer Identification (SEP-2468)

**Status: Non-gap for token-validating Resource Servers.**

RFC 9207 requires validating the `iss` claim in an OAuth authorization **response**. This is a **client-side MUST**—the OAuth client must validate that the authorization server response contains an `iss` claim matching the expected issuer.

Hangar is a Resource Server. It validates bearer tokens in authorization headers, not OAuth authorization responses. RFC 9207 does not apply to token validation.

**Relevance: Future state only.** If Hangar ever proxies or orchestrates an OAuth flow (becomes a client to an authorization server), RFC 9207 becomes applicable. At that point:

- Perform exact string comparison of the `iss` claim against known trusted issuers.
- Do not apply URL normalization during `iss` validation.

## CIMD: Client ID Metadata Documents (SEP-991)

**Status: Non-gap. Client and authorization-server concern.**

CIMD specifies how clients publish and update their metadata via a URL-based metadata document. This is relevant to clients (who publish metadata) and authorization servers (who fetch and validate it via DCR—Dynamic Client Registration, now deprecated per PR #2858).

A token-validating Resource Server does not need to:

- Perform Dynamic Client Registration (DCR).
- Fetch or validate CIMD documents.
- Maintain client metadata from any source.

Token validation requires only the issuer's public keys and trust configuration, both of which are orthogonal to CIMD.

**Relevance: SSRF caveat below.**

## SSRF Caveat: Metadata Fetching

**If Hangar ever fetches CIMD or other client-metadata documents by URL**, this introduces a Server-Side Request Forgery surface.

**Mitigation (mandatory before any such fetch):**

- Implement a domain trust/allowlist policy governing which hosts Hangar may fetch metadata from.
- Require explicit allow-listing of metadata endpoints.
- Reject all out-of-band metadata fetch requests to untrusted or internal hosts.

## What IS Hangar's Boundary

Hangar's implemented and required OAuth boundaries are:

- **RFC 9728 (Protected Resource Metadata):** Advertise server capabilities and security requirements to clients. MUST.
- **RFC 8707 (audience):** Validate the `aud` claim in bearer tokens. Accept only tokens minted for this resource. MUST.
- **Multi-issuer trust:** Support multiple trusted authorization servers and tenant-specific issuers. Shipped.
- **Cross-tenant separation:** Enforce tenant isolation via the `tenant_id` claim and per-tenant projection (see PRODUCT_ARCHITECTURE §9 for multi-tenant isolation details). Shipped.

These boundaries are distinct from RFC 9207 and CIMD.
