# CGI source comparison

Source contracts only. This report does not establish actual device behavior.

| Probe | Scope | Method | Path | Reference contract |
| --- | --- | --- | --- | --- |
| get_info.py | current | GET | `/cgi-bin/admin/admin.cgi` | MATCH |
| reset_keep_ip.py | current | POST | `/cgi-bin/admin/admin.cgi` | MATCH |
| reset_ip.py | current | POST | `/cgi-bin/admin/admin.cgi` | MATCH |
| get_import_status.py | current | GET | `/vb.htm` | MATCH |
| clear_import_status.py | current | GET | `/vb.htm` | MATCH |
| get_reload_flag.py | current | GET | `/vb.htm` | MATCH |
| upload_config.py | current | POST | `/dataloader.cgi` | MATCH |
| get_challenge.py | current | GET | `/challenge` | MATCH |
| upload_config_legacy.py | current | POST | `/dataloader.cgi` | MATCH |
| set_tcp.py | candidate | POST | `/cgi-bin/admin/admin.cgi` | MATCH |
| get_snapshot.py | candidate | GET | `/snapshot.cgi` | MATCH |
| get_log.py | candidate | GET | `/vb.htm` | MATCH |
| goto_preset.py | candidate | GET | `/vb.htm` | MATCH |
| unlock_upgrade.py | candidate | GET | `/cgi-bin/viewer/viewer.cgi` | MATCH |
| upload_firmware.py | candidate | POST | `/update.cgi` | MATCH |
| get_lpr_upload_policy.py | candidate | GET | `/cgi-bin/operator/operator.cgi` | MATCH |
| get_parking_detection.py | candidate | GET | `/cgi-bin/operator/operator.cgi` | MATCH |

## Operational differences

- Workspace Milesight get_info uses Basic; current repair code uses Digest with explicit Basic-challenge fallback.
- The independent runner selects one authentication scheme, does not warm up the root page, and sends one CGI only.
- Current app performs a multi-step import-status/identity workflow. These probes deliberately do not orchestrate it.
- Legacy RSA/AES parameters match the current and legacy Python algorithms; marker acceptance is not verified application.
- ITEC/legacy/itec/change_ip.py treats some timeouts/JSON errors as success. Probes retain unknown outcome; L2 MAC SET is a different protocol.
- Reset option=2 behavior, post-config account changes and supported model-specific candidates require physical testing.
- Current IR-CUT testing analyzes RTSP frames; it does not send a dedicated IR-CUT CGI.
- Files referenced outside the Python workspace are provenance only, not read or validated by this comparison.

The JSON report records exact reference paths and SHA256 fingerprints. The workspace sources are unchanged.
