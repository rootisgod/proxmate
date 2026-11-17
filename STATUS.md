# Implementation checkpoint — 2026-11-17

The Docker app runs at http://localhost:8088. API reference: /api/docs; OpenAPI: /api/openapi.json. The dashboard password is in an ignored local secret file; saved Proxmox and guest credentials are encrypted in the data volume. Never copy secret values into this document.

## Current iteration

Server connection UI/API (0.5.0) is deployed locally. Open Server in the sidebar to edit/test/connect/disconnect one saved host. Existing configuration was imported locally into encrypted SQLite storage, and the legacy Proxmox secret mount was removed. The eight historical jobs remain; no new live Proxmox calls or operations were performed during this iteration. SQLite/key snapshot: /data/backup-before-server-ui-20261115 (same volume, not an off-host backup).

Verification: 106 backend tests and 11 DOM workflow tests passed. Docker build and network-disabled container authentication, first-run/disconnected guards, static assets and Ansible syntax checks passed. Local authenticated checks verified imported metadata and API 0.5.0 without requesting Proxmox inventory. Real connection testing remains for the user; rendered visual/mobile verification remains outstanding.

LAN HTTP wizard fix: opening Windows or Linux provisioning failed when crypto.randomUUID was unavailable outside a secure browser context. Request keys now use crypto.getRandomValues, retaining cryptographically random, per-draft idempotency keys. A regression test explicitly removes randomUUID and opens all three wizard paths at an HTTP LAN origin. The rebuilt image includes the fix, and the running container received the identical static asset without a restart or interrupted jobs. Served-file hashes matched the tested source. Reload existing browser tabs to pick up the updated script.

Security review: the highest-risk failure was a stale VM form or saved plan applying to a replacement server. Host-bound POSTs now require the current server ID; replacement archives host-bound records/plans and changes that ID. A shared lock serializes API submissions, worker claims, scheduler ticks and connection changes; active work or enabled schedules/policies prevent changes. Passwords are encrypted/write-only; tests do not save drafts, follow HTTP redirects or echo remote errors. Residual limits: a different physical server reusing the same URL must be explicitly marked as replacement; archive restoration is manual; TLS verification can be disabled explicitly; an administrator/container compromise can access credentials. Custom scripts remain trusted administrator code.

## Previous lifecycle iteration

Local deployment: http://localhost:8088, API version 0.4.0. Historical job count: 8, all succeeded. No schedules or backup policies were created/enabled. Database/key snapshots are retained inside /data/backup-before-management-20261114 and /data/backup-before-lifecycle-20261114 (same volume, not off-host backups). Authenticated lifecycle routes, helper download, static assets and health checks passed after deployment.

Lifecycle milestone: guest bootstrap, fresh-ID backup restoration, optional isolated boot checks, reviewed retention/policies, and template families/builds/provenance/tests/promotion are implemented. Current checks: 89 backend tests and 5 DOM workflow tests passed. Windows bootstrap PowerShell parsed without execution; Linux helper shell syntax and packaged Ansible collections passed in a network-disabled container. Live guest validation remains outstanding.

Implemented the provisioning wizard, Windows answer XML/ISO, Ubuntu/Fedora/Rocky profiles, recipes, task catalog/custom scripts, encrypted credentials, guest connections/readiness, power/CPU/RAM controls, groups/bulk operations, snapshots/restore and opt-in schedules. API/reference updated.

This iteration uses local/mocked tests only. No real VM creation/start/stop, upload, guest script, snapshot/restore, backup or host/network mutation was performed. No Hyper-V permission bypass was attempted.

Backend tests cover secrets, plan immutability, duplicate submission, stale identities, credential rotation, partial failures and schedule windows/restarts. DOM tests cover navigation and review/submission. The built Docker image passes network-disabled authentication and Ansible collection syntax checks.

## Earlier live verification (historical, not repeated)

- Browser login, live dashboard and populated clone dialog checked in native Edge.
- Real Proxmox inventory and 25 local ISO files discovered.
- Created Ubuntu cloud-init template 9000, full clone 9002, and a successful compressed backup of 9002 on local storage.
- Executed Ansible ping both inside the container and over authenticated, host-key-pinned SSH to Proxmox.
- Uploaded windows_server_2022.iso (5,044,094,976 bytes), then created Windows installer VM 9001. Verified UEFI, Q35, TPM 2.0, 64 GB SATA disk, e1000 NIC and attached ISO.
- Started Ubuntu clone 9002 successfully. Its serial console reaches systemd but waits for network/DHCP. Graceful shutdown timed out during this wait; the newly created test clone was then stopped. Guest network readiness is not verified. No operations were applied to existing guests 100, 101 or 102.
- Bearer API authentication works; job history persisted across container recreation.

## Remaining real-server validation

- Confirm guest networking on the intended real server. Nested Hyper-V DHCP was not resolved; MAC spoofing remains an unverified hypothesis, and no settings were changed.
- Validate Windows installation using the actual ISO/edition, answer file and generalization. Preparation success is not installation success.
- Test a saved WinRM/SSH connection and selected tasks on explicitly approved disposable guests.
- Exercise snapshots, shutdown, CPU/RAM changes and a deliberately enabled schedule on approved targets.
- Complete visual/mobile and multi-node checks. Current new UI checks use a DOM environment, not a rendered browser.

No live mutating verification should run without explicit user approval of disposable targets. The new lifecycle features require testing against approved disposable guests/archives, including Windows driver/OOBE behavior, TLS certificate trust, Linux cloud-init vendor data, restoration and retention. Multiple users, scoped API tokens and cancellation remain outside this iteration.

The goal tracker was still paused when queried; its tools cannot resume or expand the existing goal. User authorization to continue implementation is recorded in the conversation and PLAN.md. Do not claim real-host validation complete.

Security review: the main residual risk is trusted administrator code execution. Saved task YAML rejects delegation/connection overrides, guest runs suppress output, credential reads omit secrets, and reviewed plans expose guest endpoints. Guest-agent mismatches and endpoints resolving to the configured hypervisor/loopback are rejected. These controls do not make custom scripts a sandbox or protect a compromised running container.
