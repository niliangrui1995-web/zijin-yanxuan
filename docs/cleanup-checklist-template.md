# Cleanup Checklist Template

## Scope

- Task / PR:
- Related ADR / plan:
- Target modules:

## Compatibility Bridges

- [ ] Temporary adapter / shim removed in the same batch or the next scheduled batch
- [ ] Old import path searched with `rg`
- [ ] No dead re-export remains in `__init__.py`

## Boundary Audit

- [ ] UI does not directly touch `subprocess`, `win32*`, `pyautogui`
- [ ] UI does not directly import `core.task_manager` or `core.event_bus`
- [ ] New background tasks use `infra.tasks.typed_task_registry`
- [ ] Settings changes are registered in `docs/qsettings-key-registry.md`

## File Hygiene

- [ ] Replaced helper / shim file deleted
- [ ] Obsolete docs or remediation notes deleted or archived
- [ ] New folders contain only live code/docs

## Verification

- [ ] Targeted pytest suite passed
- [ ] `tests/test_architecture_boundaries.py` passed
- [ ] `python scripts/check_utf8.py core ui vcp tests scripts app infra docs .github` passed
- [ ] `git status --short` reviewed for stray files
