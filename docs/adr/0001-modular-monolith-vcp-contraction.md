# ADR 0001: Continue Modular Monolith And Contract VCP

Status: Accepted

Date: 2026-06-22

## Context

Zijin Yanxuan is a Windows-first PyQt desktop application. The stable runtime shape is a modular monolith with UI orchestration in `ui/`, application entrypoints in `app/`, domain rules in `domains/`, infrastructure adapters in `infra/`, and legacy compatibility under `vcp/`, `core/`, and `earnings/`.

The current architecture guardrails already prevent UI code from directly importing `domains/`, `infra/`, or `vcp/` implementations. The remaining architectural risk is that new real implementation could still land in legacy compatibility modules.

## Decision

Keep the modular monolith. Do not split this desktop application into microservices or a web backend unless a separate deployment/runtime requirement appears.

Treat `vcp/` as a shrinking compatibility surface:

- New real implementation belongs in `app/`, `domains/`, or `infra/`.
- Existing `vcp.fetchers` code may be kept as a legacy implementation while callers move behind `infra` or `domains` entrypoints.
- New callers must not import `vcp.fetchers` directly outside the approved compatibility adapters.
- When touching legacy fetcher paths, prefer small adapter or strangler moves over broad file relocation.

## Consequences

Architecture work should favor boundary tightening, gradual pyright coverage, and hotspot budgets over large rewrites. Compatibility imports may remain temporarily, but every new change should make the legacy surface smaller or no larger.
