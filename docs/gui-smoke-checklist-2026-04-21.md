# GUI Smoke Checklist 2026-04-21

## Startup

- Launch the main window and confirm the workspace mounts without tab flicker or duplicate shells.
- Verify deferred startup does not block the first render.
- Confirm the title bar and command palette both initialize normally.

## Navigation

- Double-click a stock row from at least one workspace tab and verify K-line opens.
- Trigger command-palette tab switching and verify the correct tab activates.
- Confirm external quote-terminal navigation falls back to web quote pages when the native terminal is unavailable.

## Realtime / Quotes

- Toggle online mode from the main window and verify the network indicator updates.
- Verify central quotes start/stop without duplicate polling tasks.
- Confirm a table with blank quote cells can backfill quotes and market-cap data.

## Workspace Actions

- Run scan-related commands from the workspace facade path.
- Run fund-holdings sync from the command palette path.
- Verify watchlist realtime monitor can auto-start when market/session conditions are met.

## Shutdown

- Close the app and confirm startup/background tasks are abandoned cleanly.
- Confirm no stale task duplication appears after reopening the app.
