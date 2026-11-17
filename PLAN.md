# Proxmate implementation plan

## Approved lifecycle milestone: bootstrap, recovery and template versions

Implementation checkpoint: all three selected features are implemented and deployed locally as API 0.4.0. 89 backend tests, 5 DOM workflow tests, generated script syntax and isolated Docker checks passed. Existing eight-job history and credentials are preserved; no new policy is enabled. Real guest/backup/template validation remains explicitly outside this milestone's automated verification and requires approved disposable targets.

Goal: implement (1) explicit guest bootstrap during Windows/cloud-image provisioning, with exported helpers for interactive installs and subsequent connectivity verification; (2) backup restore to unused IDs, disconnected test restores, opt-in scheduled restore checks and reviewed retention; (4) template families with immutable provenance, version builds, test evidence and explicit preferred-version promotion. No IPAM, users/roles or live infrastructure mutations are part of this milestone's verification.

Modules: bootstrap.py for generated guest configuration; lifecycle.py for reviewed restore/retention/version workflows and durable policies; integrations in provisioning.py, operations.py and app.py; static/lifecycle.js plus wizard controls; API schema, docs and mocked/DOM/container tests.

Safety contracts: never overwrite an existing VM on restore; set onboot=0 and disconnect all restored NICs before any optional test boot; reject PCI/USB/shared-device passthrough in test restores. Retention defaults to preview and deletes only explicitly reviewed immutable archive identities, preserving newest/protected backups. Scheduled checks retain restored VMs for inspection and stop instead of reusing occupied IDs. Windows bootstrap opens HTTPS WinRM only to a chosen management subnet, leaves TLS verification enabled and requires trusted agent media. Template promotion requires test evidence tied to unchanged template identity, records provenance without passwords and retains previous versions. Windows generalization and manual installer completion remain explicit milestones. Bootstrap preparation is not claimed as authenticated readiness.

Sequence: validate Proxmox/API contracts; add pure bootstrap generators and provisioning integration; implement reviewed lifecycle plans, version records and policies; add dashboard flows; test collision, identity, archive replacement, network isolation, retention and promotion boundaries; build/check Docker and update local deployment only with no queued work or enabled schedules. Verify without touching live guests; preserve the existing data volume/key and interrupted-job semantics.

## Approved management expansion

Implement a guest task catalog and saved scripts, encrypted guest credentials, per-VM connection settings, VM power and CPU/RAM controls, readiness checks, reusable build recipes, optional pre-task snapshots and reviewed restore, VM groups and reviewed bulk actions, and opt-in scheduled maintenance. Keep the existing provisioning API compatible. No live VM, storage, network or hypervisor mutations are part of verification.

Sequence: add encrypted management records and frozen review plans; implement guest Ansible execution and Proxmox controls; add durable scheduling through the existing serialized queue; build management forms and integrate recipes into the wizard; verify mocked failure/scope/credential/scheduler behavior; build Docker and document limitations. New modules: management.py, guest_tasks.py, static/manage.js. Collections and WinRM support are packaged in Docker. Records and plans use SQLite plus the existing encryption key. Credential reads return metadata only. Schedules start disabled and never replay missed runs. Plans freeze exact targets and task versions; changed VM identity aborts execution. Shutdown never falls back to force stop. Restore requires a separate destructive confirmation. Failed runs retain resources and snapshots for inspection.

Verification: pytest, JavaScript syntax checks, Docker build, isolated HTTP smoke checks. Preserve the existing data volume and key. Interrupted jobs require inspection; do not replay automatically. Stop/pause schedules before upgrades. Rollback application image only after inspecting queued work; do not roll back the database or delete snapshots automatically.

## Wizard iteration

Add a four-step wizard (source, guest settings, hardware/network, review) using a shared provisioning plan API. Add a Windows XML/answer-ISO generator and Linux image catalog; keep installer-preparation success distinct from guest-install completion. Windows credentials in new queued jobs must be encrypted at rest and omitted from job responses/logs. Provide XML preview/download, default to no automatic VM start, and explicitly confirm new-disk formatting and licensing in the review step. Source media may be selected from the local read-only library or existing Proxmox ISO storage. Linux cloud profiles support release selection and DHCP/static IPv4, DNS, SSH keys and user settings.

Modules: provisioning.py, operations.py, app.py, static/wizard.js and wizard styles, API schema and tests. Verify XML and ISO round-trips, input validation, secret handling, planning without side effects and mocked provisioning order, then build Docker and inspect the UI. Do not create/start VMs, upload media or change Proxmox/Hyper-V settings during this iteration without explicit user approval. Never auto-delete partially created resources. Preserve existing action API compatibility.

Build a Docker-hosted, single-administrator LAN utility. Initial modules: Flask API and authentication, Proxmox/SSH operations, SQLite job queue, plain HTML/CSS/JS dashboard, deployment and tests. No public cloud hosting or multi-tenant access.

1. Discover host, guests, storages, bridges and ISO library without changing existing guests.
2. Implement authenticated actions, a serialized persistent queue, task polling and visible failures. Jobs interrupted by restart require operator inspection, never automatic replay.
3. Add Ubuntu cloud-image template creation, ISO installer VM creation for Windows/Linux, template conversion, full clones, backups and explicit Ansible runs.
4. Add Windows unattended installation/generalization workflow after validating available media and guest boot requirements.
5. Exercise the app in Docker; verify authentication/input boundaries and real operations using uniquely named new test resources. Browser-check main workflows.
6. Document verified behavior and remaining limitations.

Original contracts: GET /api/state provides live inventory, GET /api/jobs provides persistent progress, POST /api/jobs accepts a validated action. The initial version used mounted secrets; the server interface below supersedes Proxmox credential configuration with encrypted storage. SSH host identity is pinned. Source VM IDs must exist; target IDs must be unused. Failed creations retain their partial resources for inspection; no automatic destructive rollback.

## Server connection interface (2026-11-15)

Goal: configure, test, connect and disconnect one Proxmox server in the UI and API. No multi-server selector or live infrastructure operations in this iteration. Use existing encryption and admin authentication; preserve the task library and guest credentials on replacement.

Sequence: add server_connection.py with encrypted configuration and local legacy import; adapt operations.py and management.py to use a saved configuration snapshot; guard API submissions, worker claims and scheduler ticks against connection changes; add static/server.js and first-run setup; update Compose, API contracts and upgrade instructions; run backend/DOM and network-disabled container checks before local deployment.

Contracts: GET /api/server exposes metadata only; test/connect/disconnect POSTs require the expected saved ID. Host-bound POSTs require X-Proxmate-Server. Replacement archives host-bound records and invalidates old reviewed plans. Active jobs or enabled schedules/policies block connection edits. API tests authenticate and read nodes/version only, with no SSH calls, redirects or remote changes.

Verification: mocked connection and failure handling, encryption, auth/CSRF, stale IDs, pending work, archive isolation, reconnection, first-run DOM, password fields and obsolete dialogs. Rollback: back up SQLite plus encryption key before upgrading; preserve encrypted archives. Inspect active jobs before restoring an application/database pair. No automatic infrastructure rollback or archive restoration.

Completion checkpoint (2026-11-17): server UI/API implemented and deployed locally; prior settings imported and legacy secret mount removed. The subsequent HTTP LAN wizard failure was reproduced and fixed by using getRandomValues for request keys. Checks cover 106 backend tests and 11 DOM workflows, including missing randomUUID and stale server responses. Real-server and rendered-browser validation remain outstanding; no new live Proxmox operations were performed for these changes.
