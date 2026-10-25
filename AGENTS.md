# FlexClock — LEDSign project instructions

## Repository boundaries

- `/home/admin/sign-controller` is the `pril-debug/LEDSign` repository.
- `web/` is a separate Git repository: `pril-debug/LEDSign_Site`.
- Commit website files in the website repository; commit installers, system helpers, and main-project documentation in LEDSign.
- Never add the nested website repository as ordinary files or change its relationship to the parent repository accidentally.
- Preserve unrelated changes, device configuration, credentials, and customer assets.

## UI Rules

1. Always use well-formatted application modals. Never use browser/system dialogs such as `alert()`, `confirm()`, or `prompt()`.
2. Never use expandable sections, accordions, or `<details>` elements to hide settings or device information.
3. Use plain English throughout the interface.
4. Never display raw Linux commands or system output to ordinary users.
5. Do not fill pages with unnecessary descriptions, explanations, disclaimers, or technical notes.
6. Keep error messages short, specific, and useful.
7. Use a white background and consistent modern styling.
8. Use colored status pills for connected, disconnected, running, stopped, warning, and error states.
9. Display useful information directly without requiring additional clicks.
10. Use readable font sizes, good spacing, and clearly separated sections.
11. Never add buttons or controls that do not work.
12. Do not claim a setting was saved until the underlying operation succeeds.
13. Use consistent navigation, form layouts, buttons, and validation.
14. Support desktop, tablet, and mobile screens.
15. Never put developer-facing diagnostics on normal configuration pages. Keep commands, raw system output, helper filenames, stack traces, commissioning instructions, and implementation details in developer logs or documentation. Show users only plain-English settings, accurate status, and short, actionable errors.

## Development Rules

1. Inspect existing code before changing functionality.
2. Preserve working features.
3. Fix identified problems rather than adding workarounds.
4. Verify actual functionality, not just successful HTTP responses.
5. Use automated tests for backend changes.
6. Test the real Pi when hardware or operating-system behavior matters.
7. Never invent configuration values, service states, or test results.
8. Keep existing network connectivity intact during development.
9. Do not reboot or restart network services without approval.
10. Update `setup.txt` and installation scripts whenever system setup changes.
11. Keep both GitHub repositories compatible.
12. Use clear, concise commit messages.
13. Keep final reports brief: changes, tests, remaining problems.
