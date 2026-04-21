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
- Key runtime metrics are now recorded through `core/observability.py`.

## Automated Metrics

- `main_window_first_paint_ms`: recorded on first `MainWindowQT.showEvent()`
- `workspace_mount_ms`: recorded when `ApplicationBootstrap` mounts the workspace
- `startup_deferred_load_ms`: recorded when deferred cache/bootstrap work completes
- `startup_asian_sync_ms`: recorded when silent Asian-market sync completes
- `smart_startup_network_probe_ms`: recorded for the startup network probe
- `quote_refresh_batch_size`: recorded for each central-quote polling batch
- `quote_refresh_ms`: recorded for each central-quote polling batch
- `kline_open_ms`: recorded when a K-line window is opened
- `kline_active_windows`: recorded after each K-line window open

## Structured Logging

- Structured log events are emitted through `core/observability.emit_structured_log`.
- Current key events:
- `workspace.mounted`
- `startup.deferred_load.completed`
- `startup.asian_sync.completed`
- `startup.network_probe.completed`
- `quotes.refresh.completed`
- `main_window.first_paint`
- `kline.opened`

## Follow-up Metrics To Record

- P50 / P95 launch-to-interactive
- P50 / P95 workspace mount time
- K-line open success rate
- Duplicate background task incidence
