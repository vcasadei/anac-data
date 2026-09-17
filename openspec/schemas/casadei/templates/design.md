## Context

<!-- Current state and constraints that shape the approach. See proposal.md for motivation - don't restate it -->

## Goals / Non-Goals

**Goals:**
<!-- What this design aims to achieve -->

**Non-Goals:**
<!-- What is explicitly out of scope -->

## Decisions

<!-- Key design decisions with rationale and alternatives considered.
     - An abstraction must name the concrete second use case that justifies it.
     - A departure from an existing project convention must say why.
     - Any third-party dependency not already used, with alternatives considered
       and confirmation the user approved it at proposal time. -->

## Security & Observability

<!-- Delete if the change touches none of these.
     - Where secrets load from (env vars or vault; never hardcoded).
     - How personal data is masked or redacted before logs/metrics/telemetry.
     - How correlation IDs and OpenTelemetry headers propagate across service
       and queue boundaries. -->

## Migration Plan

<!-- Mandatory when the change alters a database schema; delete otherwise.
     - Expand-and-contract steps, each separately deployable, for zero downtime:
       expand (add new shape, dual-write) -> migrate -> contract (drop old shape).
     - A functional `down` migration for every step. A migration without a
       tested rollback is not finished.
     - Feature flag name, default value, and removal criteria for high-risk
       workflows or significant refactors. -->

## Risks / Trade-offs

<!-- Known risks and trade-offs. Format: [Risk] -> Mitigation -->

## Open Questions

<!-- Genuinely deferrable unknowns only - things that can be answered later
     without changing the specs, the approach, or the task breakdown.
     Anything that would change those, resolve now by asking the user.
     Omit this section if none. -->
