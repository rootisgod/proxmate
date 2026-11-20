# Proxmate implementation plan

The [everyday Windows workflows](docs/windows-workflows.md) describe the intended template, clone and ISO experience, acceptance criteria and disposable-guest validation.

## Windows workflow implementation

1. Extend the existing Windows build wizard and encrypted simple-build plan with a source choice. For a template, freeze its node, VM ID, configuration identity and clone mode. Clone to a fresh ID while stopped, attach first-boot answer media, then start. Use a specialize/oobe-only answer for clones. Verify the requested Windows hostname and per-guest TLS login through the existing restart-safe readiness wait before running follow-up tasks. Full copies are the default.
2. Classify Windows ISO filenames in the picker and verify the edition using actual WIM/ESD metadata. Keep unrecognized media browsable. Record generated XML and companion PowerShell at the media-generation boundary in encrypted job artifacts, including on failure. Offer a reusable download and a separate, explicit original download. Support opt-in manual updates and named inputs on saved Ansible task lists.
3. Review optional NTFS data disks by size, label and drive letter. Allocate fresh disks with unique serials on the new VM before boot. Verify the VM, recorded Proxmox volume, guest serial, size and partition state before formatting. An uncertain mutation ends in an inspectable job.
4. Add targeted WinRM diagnostics and a reviewed, subnet-scoped HTTPS setup action to the machine page, with console instructions for guests lacking an agent. Show historical verification in inventory using local records. Keep GitHub library synchronization as a later integration.
5. Verify with temporary databases, fake Proxmox/guest endpoints, generated XML and PowerShell, browser workflow tests, and a network-isolated Docker image. Roll back the application image with the queue idle. Keep the data volume and retained guests for inspection. Live Windows install, Sysprep and disk-format validation requires disposable targets.

The four Windows workflow changes are implemented. The automated acceptance checks exercise both clone modes, stale guest identity and hostname, archived answers after a failed upload, disk ownership and repeat runs, and the reviewed WinRM setup path. Live Windows acceptance is tracked in the workflow spec.

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

## Simple Windows Server and Ubuntu flows

Goal: one guided flow chooses an OS, machine count and software, discovers the host resources, creates new guests, configures remote access and applies frozen guest tasks. Existing VMs keep a separate simple script-selection path. Advanced tools remain available but leave the main navigation. Windows editions must be read from ISO metadata; never infer their indexes. Ubuntu defaults to the official cloud image for unattended setup. New templates are prepared and generalized in the same job; failures retain resources for inspection.

Implementation: add read-only ISO inspection and a simple-flow planner/executor; generate Ubuntu seed media without requiring snippets storage; reuse encrypted credentials, authenticated bootstrap and guest-task execution; add Windows-feature tasks; introduce a three-page wizard and compact primary navigation. API plans freeze exact new IDs, media identity, generated credentials and task bodies. Confirmed execution queues one tracked job per machine so partial results are visible and duplicate submissions cannot create extra VMs.

Validation: mock discovery, ISO indexes/malformed media, automatic resource selection, batch conflicts, secret omission, task execution order and template cleanup; DOM-check the combined wizard and old advanced flows; inspect available local ISO metadata and connected host inventory read-only. Do not create/start/stop guests, upload media, run scripts or modify host storage during development. Back up local data before deployment and require an idle queue before restarting. No automatic rollback or deletion of failed VMs.

Implementation checkpoint: 0.6.0 deployed with 129 backend and 15 DOM checks passing; packaged Ansible syntax and generated PowerShell parse checks passed. Local Windows Server 2022/2025 edition metadata and live resource discovery verified read-only. The user supplied nuc's console-verified SSH fingerprint; it was saved and pinned SSH inspection of the remote Windows Server 2025 ISO succeeded, clearing the connection blocker without bypassing trust checks. Real guest installation/generalization validation still requires explicitly approved disposable targets.


## Windows remote access before login

Goal: configure HTTPS WinRM before guest-tool installation, retry bootstrap as SYSTEM without interactive/automatic login, and report the actual readiness stage. Keep pinned certificate verification, scoped firewall access and the authenticated readiness gate. Do not restart or alter the current VM during implementation.

Sequence: extract the Windows helper into a packaged PowerShell template; add protected local state, startup/timed retries and independent WinRM/guest-tool stages; keep certificates stable across retries; defer MSI while Windows Setup is active; update clone cleanup and readiness messages; exercise the orchestration with mocked Windows operations, parse the actual generated scripts, and run focused backend/container checks. Existing answer media does not gain the change automatically; deploy only when the queue is idle. Failed builds remain available for inspection.

Files: bootstrap.py, windows_bootstrap.ps1, Dockerfile, lifecycle.py, template_finish.py and tests/docs. No public API or database schema change. Rollback is the previous container image; do not disable TLS checks or delete failed guests automatically.

Validation checkpoint: user-approved replacement nuc/104 completed Windows/WinRM/Chocolatey/7-Zip without an interactive login. Live inspection exposed an unsigned community MSI and an already-installed serial-driver retry; MSI checksum pinning and driver idempotence were added and applied through verified WinRM. The worker then completed the original job successfully. Final image deployed after queue idle; 166 backend, 13 mocked PowerShell and container checks passed. A subsequent user-requested clean recreation (job a150235eeb2443bfb426b76cce0540c4) verified the full install with the final hash fix present from the start: no interactive login or repair, WinRM/QGA ready, Chocolatey and 7-Zip 26.06 installed.
