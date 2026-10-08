# Agent instructions

These rules apply to all work in this project. The user's requirement is an
English-based architecture with mandatory language packs and support for
multilingual operating systems.

Read [docs/AGENT_LESSONS.md](docs/AGENT_LESSONS.md) before changes involving
startup, updates, networking, persistence or inspection transitions. Apply the
listed regression checks when touching the corresponding failure boundary.

## Architecture and localization (mandatory)

- Build and validate the architecture in English first. Use English identifiers,
  stable state/error codes, API keys, protocol fields, configuration keys and
  diagnostic events. Do not make business logic depend on translated text.
- Language packs are required, not an optional later enhancement. Every new or
  changed user-facing message must be obtained through a shared translation
  function using a stable English message key. This includes buttons, status
  prompts, settings, dialogs, validation errors, updater messages and startup
  failures. Do not embed Japanese, Korean or Chinese UI text in application logic.
- Keep language resources separate from Python code, for example UTF-8 JSON files
  under `locales/`. Maintain a complete English reference pack and a Japanese pack
  for the existing operator workflow. Any additional UI language must have its own
  pack. A Korean Windows environment does not by itself require Korean UI.
- Preserve the operator's explicit UI-language selection across restarts and
  updates. OS language must not override a saved preference. Unsupported locales
  and missing translation keys fall back to English without preventing startup.
- Keep placeholders named and consistent between packs. Translate complete
  messages rather than concatenating translated fragments. Log missing keys for
  diagnosis without displaying internal implementation details to operators.
- Persist stable result/reason codes separately from their localized display
  labels. Preserve compatibility with existing records and GAS consumers; do not
  silently rewrite historical inspection results while migrating localization.
- Existing hardcoded UI strings are migration work. When changing a screen or
  flow, move the affected strings to language packs. Do not report the application
  as fully localized until startup, updater, settings and inspection flows have
  been covered and verified.

## Multilingual Windows compatibility (mandatory)

- Use explicit UTF-8 for text files, language packs, JSON/YAML, logs and reports.
  Configure redirected Python output and child-process environments for UTF-8;
  never rely on the system ANSI code page (including CP932/CP949).
- Support Unicode and spaces in user names, install paths and Python executable
  paths. Use `pathlib`, argument lists and correct quoting. UTF-8 path-setting
  files must support both BOM and no BOM.
- Use available system fonts with Unicode fallback. Do not require a font that
  exists only on Japanese Windows. Verify translated text fits controls at common
  DPI/scaling settings and across supported panel modes.
- Keep startup, updating and inspection in Python, with `start.pyw` as the default
  GUI entry point. Do not introduce a custom EXE requirement or console window.
  `start.vbs` is only a compatibility entry point for missing `.pyw` associations.
- Startup failures must produce a useful dialog and UTF-8 diagnostic log. Do not
  suppress failures because the GUI process has no console. Do not disable or
  bypass Windows security settings to achieve compatibility.

## Verification and delivery

- Verify language-pack key coverage and matching placeholders; test English
  fallback, unsupported locales and persisted language selection when localization
  behavior changes.
- For startup/encoding changes, test a CP949 parent environment, Unicode/spaced
  paths, and the actual `pythonw` entry point. Keep the CP949 regression test.
- Distinguish simulated locale tests from testing on a real Korean Windows PC.
  Do not claim verification on an OS that was not available.
- Verify old-launcher compatibility before publishing update manifests. Entry
  points not accepted by older updater allowlists must be distributed in the
  initial ZIP rather than breaking automatic updates.
- Language packs must be included in distribution packages and the updater's
  allowed resource paths/manifest when introduced. Keep their schema compatible
  with the corresponding application version.
- Never publish local passwords, camera config, production records, logs or
  inspection images. Preserve those files during remote updates.
