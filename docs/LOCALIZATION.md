# Localization architecture

Backend messages, diagnostic logs, state codes, settings keys and protocol values
are English. The native window and updater translate their display text through
`src/i18n.py`. Translation never modifies stored backend values or log records.

Language packs are mandatory resources in `src/locales/en.py` and
`src/locales/ja.py`. Each pack contains only a UTF-8 `MESSAGES` dictionary with
matching stable English keys and named placeholders. Python resource files are
used so previous updater allowlists can accept the new packs without an upgrade
deadlock. Keep business logic out of these files.

Select English or Japanese under Settings -> Display -> Language. The saved
`ui_language` preference controls both the updater and inspection window. The
existing operator workflow defaults to Japanese when no preference is saved;
unsupported locales and missing Japanese entries fall back to English. Windows
display language does not change inspection language or technical logs.

Static and templated English display messages are resolved to pack keys by the
shared translator. Third-party exception text, camera models and device data
retain their original content when no controlled translation exists. Do not
translate or interpret those values as workflow state.

New direct NG records include a stable `ng_reason_code`. Existing CSV rows are
retained. Existing GAS field names and evidence prefixes remain compatible.

Before release, run language-key/placeholder checks, fallback and settings tests,
CP949 startup regression, native UI self-tests in both languages, the inspection
transition suite and old-updater manifest validation. Language packs must be
present in both the source distribution and update manifest.
