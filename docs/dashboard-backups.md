# Back up or restore Proxmate

## Specification and implementation plan

Protect the work stored in the dashboard and let an administrator move reusable definitions to another cluster. Both operations belong under **Resources → Backup & restore** and must work on a new dashboard before its first connection.

| Backup | Contents | Restore behavior |
| --- | --- | --- |
| Complete dashboard | All persistent application database tables, including scripts, credentials and defaults, server connection and archives, guest bindings, builds, answers, schedules, policies and job history | Replace the destination dashboard in one SQLite transaction. Re-encrypt secrets with the destination key, disconnect the recovered server, disable schedules and policies, interrupt unfinished work and invalidate outstanding execution approvals. |
| Portable configuration | Custom library scripts, current lab packs with their embedded scripts, and saved guided builds with portable snapshots | Preview each item, select new definitions and merge them. A saved build becomes a single-machine lab so the operator can choose destination media, placement and logins at deployment. Retain existing definitions and report name conflicts. Show saved builds missing portable snapshots. |

1. Add a versioned, size-bounded, passphrase-encrypted file format and authenticated export, preview and restore endpoints. Use the existing database, lab-pack reader and script validation. No uploaded SQL or filesystem paths are executed.
2. Bind the preview to the encrypted file and destination state. Require explicit confirmation, dashboard password for full export and replacement, and an idle destination before replacement. Keep preview free of writes. Recheck inside the restore transaction.
3. Add download and restore workflows to the dashboard, Help and API docs. Keep files and passphrases out of logs, browser storage and server-side staging.
4. Verify full recovery with different encryption keys, portable migration between different server IDs, conflict handling, stale previews, wrong passwords, tampering and rollback. Exercise the published image locally before live deployment.

## Recover the same lab

Choose **Complete dashboard**, enter a backup passphrase of at least 12 characters and your current dashboard password, then download the file. Keep the passphrase separately. The export takes a consistent database snapshot while Proxmate is online. Use HTTPS or a trusted local connection for this administrator workflow.

On the destination, sign in, select the file, enter its passphrase and choose **Preview restore**. Check the source, counts and destination. Download the destination's own complete backup first if you might need to undo the replacement. Finish active jobs and disable enabled schedules and backup policies. Confirm replacement and supply the destination dashboard password. After restore, open **Server settings**, verify the recovered address and reconnect. Inspect any interrupted work on Proxmox before retrying it. Re-enable the schedules and policies you want.

The destination keeps its dashboard password, API token, session and encryption keys. VM disks, ISO files, the Compose file, container environment, browser preferences and manually placed files in the data volume belong to the installation. Keep a separate CT or volume backup for those files and installation identity. Stop the old dashboard before enabling automation on its replacement.

## Move to another cluster

Choose **Portable configuration** and download the encrypted file. On the destination, connect the intended Proxmox server before importing labs. Custom scripts can be imported into a disconnected dashboard. Preview the file, select the definitions you want and choose **Import selected**. Matching definitions are skipped. For a conflicting definition, rename it in the source or destination and preview a fresh export. Imported labs appear under **Labs**. Review local ISOs, templates, target machines and login choices when deploying. Importing a definition creates no guests or jobs.

Reusable scripts and lab files include their original code. Review them for embedded addresses or hard-coded credentials before sharing a portable backup. Credential records, VM bindings, job history, schedules and private installation inputs stay local in this workflow. Historical saved builds without a portable snapshot can be recovered in complete mode or re-reviewed and saved before migration.

## File and recovery contract

Format 1 uses scrypt and AES-256-GCM with fresh random salt and nonce for every export. The encrypted file limit is 64 MiB. For larger databases use an offline data-volume backup. Keep a matching Proxmate version available for disaster recovery. Unsupported formats or database layouts fail before replacement. A cancelled or failed restore leaves the previous dashboard data in place.
