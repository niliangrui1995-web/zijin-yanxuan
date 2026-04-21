# Performance Baseline 2026-04-21

## Focus Areas

- Main window first paint after launch
- Workspace mount time
- Deferred cache bootstrap latency
- Central quote polling stability
- K-line open responsiveness

## Baseline Capture Method

1. Start with local cache present and network available.
2. Measure launch to first interactive main window.
3. Measure workspace mount completion.
4. Measure command-palette invocation latency after launch.
5. Measure a single stock K-line open path.

## Current Guardrails

- Startup heavy work is deferred into `StartupOrchestrator`.
- Realtime polling is centralized behind `CentralQuotesService`.
- Cross-tab command routing is funneled through `ApplicationBootstrap` and `WindowCommandService`.
- Shared finance / market-cap refresh uses the typed shared task key `shared_market_caps`.

## Follow-up Metrics To Record

- P50 / P95 launch-to-interactive
- P50 / P95 workspace mount time
- Average quote refresh batch size
- K-line open success rate
- Duplicate background task incidence
