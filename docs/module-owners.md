# Module Owners

This registry records the default owner for each stable module boundary introduced by the architecture upgrade plan.

## Rules

- New cross-module changes should notify the listed owner first.
- Boundary changes must update this file in the same patch.
- Compatibility shims do not become owner entrypoints; the target stable module keeps ownership.

## Owner Map

| Module | Scope | Default owner |
| --- | --- | --- |
| `app/bootstrap` | window bootstrap, startup assembly, host contracts | Architecture lead |
| `app/services` | cross-context orchestration entrypoints for UI | Architecture lead |
| `app/use_cases` | command/use-case composition | Desktop lead |
| `domains/scan` | VCP rules, indicators, RPS, breakout decisions | Engine lead |
| `domains/quotes` | quote normalization, snapshot merge, finance enrichment payloads | Engine lead |
| `domains/watchlist` | watchlist state and rules | Desktop lead |
| `domains/fund_holdings` | holdings compare, store, sync | Engine lead |
| `domains/earnings` | earnings engine and scheduler | Engine lead |
| `domains/market_calendar` | exchange calendars and quote windows | Architecture lead |
| `infra/market_data` | local history, realtime quotes, adjustment adapters | Engine lead |
| `infra/settings` | settings schema, repository, migration | Architecture lead |
| `infra/tasks` | task scheduler, typed task registry, process runner | Architecture lead |
| `infra/navigation` | external terminal/window automation | Desktop lead |
| `infra/storage` | data-store adapters | Architecture lead |
| `infra/events` | UI signal bridge and event adapters | Architecture lead |
| `ui/shell` | shell chrome, title bar, system menu | Desktop lead |
| `ui/workspaces` | workspace assembly and facade services | Desktop lead |
| `ui/tabs` | tab presentation and interaction | Desktop lead |
| `ui/workers` | UI-bound workers and polling loops | Desktop lead |

## Review Gate

- If a change touches more than one owner scope, treat it as an architecture change.
- If a UI file needs a new infrastructure capability, add or extend an `app/services` or `app/use_cases` entrypoint instead of importing legacy modules directly.
