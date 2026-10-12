# Independent camera CGI validation

The production inspection flow is unchanged. These files separate request
contracts from sequencing so each CGI can be tested before rebuilding the logic
tree. All output is technical English and UTF-8. No request is sent by importing
a module or running it without `--execute`.

## Inventory

Current application commands (9):

| File | Request | Actual effect to establish |
| --- | --- | --- |
| `get_info.py` | GET admin.cgi / get.system.information | SN, model, firmware and accessible account |
| `reset_keep_ip.py` | POST admin.cgi / set.system.reset / options=3 | Reboot; retained IP/mask/gateway/account |
| `reset_ip.py` | POST admin.cgi / set.system.reset / options=2 | Actual resulting IP/mask/gateway/account; confirm by MAC |
| `get_import_status.py` | GET vb.htm?&getimportstatus | Meaning and timing of 0/1/2/3 |
| `clear_import_status.py` | GET vb.htm?setimportstatus=0 | Before-upload clear versus after-success acknowledgement |
| `get_reload_flag.py` | GET vb.htm?&reloadflag | Readiness behavior; does not prove config application |
| `upload_config.py` | POST dataloader.cgi?up=cfg&pwd=... | Import acceptance, status progression and actual settings/account changes |
| `get_challenge.py` | GET challenge?PublicKey=... | Valid XML key/keyID and session behavior |
| `upload_config_legacy.py` | POST dataloader.cgi?up=cfg&keyId=...&encryptPwd=... | RSA/AES compatibility and config application |

Workspace camera candidates (8), not currently used by the application:
`set_tcp.py`, `get_snapshot.py`, `get_log.py`, `goto_preset.py`,
`unlock_upgrade.py`, `upload_firmware.py`, `get_lpr_upload_policy.py`,
`get_parking_detection.py`. Their presence in old sources does not prove support
on NC8164-FPE. Gateway/NVR APIs are different products and are not copied here.

The current IP movement is L2 Ethernet, not CGI. RTSP/video-based IR-CUT checking
is also not CGI. Keep these separate when rebuilding the logic tree.

## Running individual probes

Show the contract, without a target or connection:

```powershell
python cgi/get_info.py
python cgi/reset_ip.py
```

Preview a fully encoded request; required missing values are placeholders:

```powershell
python cgi/upload_config.py --base-url http://192.168.0.150
python cgi/reset_keep_ip.py --base-url http://192.168.0.150
```

Send only the selected request:

```powershell
python cgi/get_info.py --base-url http://192.168.0.150 --execute --profile before
python cgi/get_info.py --base-url http://192.168.0.150 --execute --profile after
python cgi/get_import_status.py --base-url http://192.168.0.150 --execute
```

Use the camera's actual current address. Before/after accounts come from the
project's private `records/settings.json`. `--user` overrides the username;
`ITC_CAMERA_PASSWORD` overrides the access password; otherwise a missing password
is requested interactively. `--auth basic` or `--auth digest` selects exactly one
scheme. There is no automatic auth fallback or extra root-page warm-up request.

Query/body mappings are read from UTF-8 JSON using `--params-file` and
`--body-file`. Reset probes already fix their `options` value. Multipart upload
requires a nonempty file:

```powershell
python cgi/upload_config.py --base-url http://192.168.0.150 --file "config file/config.bin" --execute
python cgi/reset_keep_ip.py --base-url http://192.168.0.150 --execute
python cgi/reset_ip.py --base-url http://192.168.0.150 --profile after --execute
```

Modern import `pwd` comes from `ITC_CONFIG_PASSWORD` or the saved
`config_import_password`, unless explicitly supplied in the private parameter
file; a missing password is requested interactively only with `--execute`.
Legacy upload requires the matching session's `keyId` and `encryptPwd`.
`legacy_crypto.py` exposes offline helpers reproducing the RSA-1024 exponent-3,
double Base64 modulus, PKCS1v15, AES-CBC and PKCS7 sequence. Keep the returned RSA
private key in memory between challenge construction and password encryption.
The helpers never upload or retry anything.

For a practical legacy handshake test, this command sends only the challenge
GET, keeps the RSA private key in memory, then saves derived session parameters:

```powershell
python cgi/get_challenge.py --base-url http://192.168.0.150 --execute --session-file data/cgi_validation/legacy_session.json
```

Check the saved report and test result before running the separate upload:

```powershell
python cgi/upload_config_legacy.py --base-url http://192.168.0.150 --params-file data/cgi_validation/legacy_session.json --file "config file/config.bin" --execute
```

Treat the session file as private. Do not reuse it on another camera or after the
device's session expires. For a snapshot, add
`--save-binary data/cgi_validation/snapshot.jpg` to `get_snapshot.py --execute`
to inspect the actual image; invalid images are not saved as snapshots.

The probes do not poll another CGI, verify another identity endpoint, change IP
after uploading, retry writes or choose the next camera. Record those steps as
separate tests. `--execute` on reset/upload/PTZ/network/upgrade commands can change
the device; choose one identified test camera and do not run against an active job.

## Evidence and next-day verification

Executed requests write redacted JSON under `data/cgi_validation/`. Each record
contains command, method, URL, account profile, HTTP status, decoded response,
response SHA256 and a conservative interpretation. Password/query secrets are
masked; config/firmware content is never printed. Snapshot replies record binary
hash and image decoding, not text. `--record` selects another evidence path.

`physical_effect_verified` remains false in automatic reports. A reset HTTP 200,
upload HTTP 200, readiness flag or success marker is not physical verification.
Timeout after a modifying request is an unknown outcome; the tool never resends.

Fill `validation_template.json` with observed MAC/SN/model/firmware, before/after
IP and credentials **profile names only**, actual effect, elapsed time and
evidence paths. Keep filled production sheets under `data/cgi_validation/`, not
beside the public template. Include separate clear-status tests before import and
after status 1. The original template stays NOT_TESTED.

Once interfaces are verified, use the measured prerequisites, effects and
completion conditions to build the new logic tree. This task does not declare
CGI stability or redesign the current workflow.

## Source comparison

`COMPARISON.md` summarizes matches and operational differences.
`comparison_report.json` records source paths and fingerprints.
Rebuild the comparison without connecting to a device:

```powershell
python cgi/compare.py --workspace "C:/Users/ryu07/Dropbox/Python"
```
