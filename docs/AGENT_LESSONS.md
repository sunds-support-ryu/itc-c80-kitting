# Agent lessons from this project

Read this before modifying startup, updates, networking or the inspection flow.
These are project-specific engineering lessons, not proof that every related
failure has the same cause.

| Observed failure | Established cause / evidence | Prevention |
| --- | --- | --- |
| Startup failed on Korean Windows | CP949 could not encode Japanese bootstrap output; traceback identified `print` | English technical logs, explicit UTF-8 streams and child environments, CP949 regression test |
| Mojibake in logs | Writer encoding differed from the UTF-8 log reader | Explicit encoding at every text boundary; preserve original third-party data |
| Custom EXE removed by antivirus | User reported detection; the actual threat name was not provided, so false positive was never established | Use Python GUI source distribution; do not disable antivirus or claim an unverified false positive |
| Different Python used after launch | PATH/Store aliases could select a different interpreter than the source launcher | Reuse `sys.executable`; let explicit configuration override it; record interpreter path/version |
| A downloaded launcher change did not affect the running launcher | Python retains already imported modules in the current process | Critical bootstrap fixes must work when invoked by the previous launcher; restart to activate launcher changes |
| New manifest rejected by old updater | A new VBS entry was outside the old updater's allowlist | Test manifests against the previous updater before publishing; distribute stable entry scripts in ZIP |
| Config action required unexpected extra confirmations | Earlier IR ON/OFF manual stages no longer matched the requested flow | Test RTSP auto pass -> cover/uncover auto pass -> purple manual decision -> exactly one config dispatch |
| Before-config authentication returned 401 | An authorized real device accepted the after-config account and matched SN | Separate account profiles; retain verified per-camera credentials; do not mutate shared credentials during parallel work |
| Upload response alone did not prove application | HTTP 200 is insufficient; real success included import status 1 and post-import SN verification | Verify firmware status and identity before final IP changes; never automatically resend an uncertain upload |
| L2 discovery succeeded but HTTP was unreachable | Broadcast discovery and IP communication have different reachability requirements | Verify work-IP placement separately from L2 discovery; bind MAC/SN before writes |
| One adapter had multiple IP addresses | An adapter cannot be identified by one displayed IP | Identify by adapter ID/MAC; choose a usable address/subnet from all assigned addresses |
| A working camera was discovered again | Discovery continues while work runs | Hold per-MAC work slots, prevent duplicate SET/import dispatch and release only on confirmed removal or explicit rekit |
| Atomic JSON replacement raised WinError 5 | Replacement failed on Windows; fixed temp names and external file occupation require distinct investigation | Unique temp files, serialized mutations, bounded retries, visible save failure; never silently discard job state |
| Tests passed without proving physical inspection | Offline tests simulate frames and human actions | State exactly what was simulated; actual purple inspection and label confirmation require human evidence |

## Language migration lessons

- Business decisions must use English state/reason codes, not localized widget
  text. Localize only at the display boundary; keep diagnostic logs English.
- A translated combobox label is not its internal value. Use selection indexes or
  stable IDs for defect category and language selection.
- Language changes must not rewrite historical records or credentials. Existing
  Japanese rows are historical data, not untranslated new log messages.
- Package the reference and Japanese language packs with the program. Check key
  coverage, named placeholders, fallback, settings persistence and old-updater
  compatibility before release.
- Never transfer local production logs, passwords or config files into public
  commits while collecting debug evidence.
