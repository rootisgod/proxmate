# Proxmate

A Docker-hosted LAN dashboard and HTTP API for Proxmox. Single-administrator access; no external account or hosted service required.

See [project status](STATUS.md) for the deployed revision, live checks and remaining acceptance tests.

## Run

Docker Compose is required. Copy `.env.example` to `.env`, set your ISO directory, and create `.env.admin` containing a separate, strong password for the Proxmate dashboard. This ignored file is mounted as a Docker secret. A Proxmox password file is no longer required for a new installation.

```sh
docker compose up -d --build
```

Open **http://localhost:8088** (or `http://YOUR-DOCKER-HOST:8088` from your LAN), and sign in with `.env.admin`. Use `PORT` in `.env` to change the host port. The container always listens on 8080. Allow LAN traffic through your host firewall as appropriate.

Choose **Server** in the sidebar. Enter the HTTPS address, username including realm (for example `root@pam`) and password. **Test connection** checks API authentication and visible nodes without saving. **Save & connect** tests again and stores the connection encrypted in the data volume. Passwords are never returned by the API; a blank password retains the existing value only for the same address and username. TLS verification defaults on; explicitly uncheck it only for a trusted LAN server with a self-signed certificate. HTTPS is required. SSH fingerprint and username are separate optional settings; verify the SHA256 fingerprint independently from the server console. Linux image builds and advanced hypervisor scripts require SSH access using the saved password. The connection test checks the HTTPS API only.

**Disconnect** disables Proxmate operations without stopping or changing VMs. Finish queued, waiting and running jobs and disable schedules and backup policies before disconnecting or editing the connection. Reconnect using the saved fields. To use another server, enter its details and tick **Replace the server**. This archives the old server's VM connections, groups, recipes, schedules, template/recovery metadata and reviewed plans. Your scripts, guest credentials and job history remain. Also tick replacement if a different machine reuses the old address. Archives are encrypted in the data volume; restoring them currently requires manual administration. There is one configured server, not a multi-server selector.

Upgrade from environment-based configuration: retain the existing `.env` and `.env.password`, explicitly set `PROXMOX_URL` to your current server address in `.env`, then run `docker compose -f compose.yaml -f compose.legacy.yaml up -d --build` once. Startup imports the existing connection locally, without contacting Proxmox, only when no saved connection exists. After checking **Server**, run `docker compose up -d --no-build` to remove the legacy password mount. Saved UI settings take precedence thereafter. Keep the data volume and its session key together; losing the key makes saved credentials unreadable.

## Build machines

Choose **Save this build** at the review step to give a Windows, Ubuntu or Debian configuration a name. Find it under **Create → Saved builds → Use build**. In **Activity**, a guided build's job log also offers **Create another like this** and **Save this build** when its settings are available. Enter a new machine name and any required guest password. Review the newly allocated VM ID, installation source, current library tasks and inputs before creating it. Removing a saved build only removes that shortcut.

If guest software fails during a build or an Operations run, open its job in **Activity** and choose **Review remaining tasks** beside the affected VM. The review shows the saved login, the original task versions and inputs, and any snapshot or template preparation the run will perform. Inspect a task marked as already started before approving it again, especially after a timeout. Each recovery gets its own job linked to the earlier run. Recovery requires a recorded task checkpoint and the original VM and disks. Installation and template-generalization failures still need inspection.

For a guided Ubuntu or Debian VM, paste your workstation’s **public** SSH key in Machine details if you want to sign in from your own terminal. Proxmate also installs a separate automation key. A successful VM build offers **Open desktop** for RDP-enabled Windows or **Copy SSH command** for Linux in the completion notification. The machine page also offers these actions for running guests. The dashboard checks the guest identity and current address on click. Open the downloaded `.rdp` file with your Remote Desktop client and sign in there. The SSH shortcut uses your local SSH client and keys.

### Debian 13

In **Create**, choose **Debian** under **Virtual machine** or **Reusable template**. Proxmate selects the newest discovered Debian 13 amd64 DVD-1 or netinst ISO. Enter a name, optionally add your GitHub username, public key or working-VM password, and select APT packages or library tasks. The installer verifies the new system disk's serial before partitioning. The guest needs DHCP and access to Debian package repositories.

Open the finished job to download its reusable preseed. A prepared Debian template appears in Create. Each full clone gets its own hostname, OS machine ID and SSH host keys. Use **Operations** to run additional scripts with the saved login.

To exercise the complete workflow on a lab node, run the opt-in test from PowerShell. It creates one working VM, one template and two full clones, tests password and key sign-in, and runs a parameterized Ansible task through Operations. It retains all four machines and reports their dashboard job IDs.

~~~powershell
$source = Read-Host 'Debian ISO ID from the dashboard'
Get-Content -Raw tests/live_debian_build.py | docker compose exec -T proxmate python -u - --node nuc --source $source
~~~

Append `--templates-only` to repeat the template and two-clone checks when the ordinary VM build has already passed.

### Ubuntu Server 24.04 ISO

Choose **Create → Virtual machine → Ubuntu** for a working VM, or **Create → Reusable template → Ubuntu**. Proxmate discovers Ubuntu Server 24.04 amd64 live-server ISOs on the selected node and in the local library. **Install from** also offers the official cloud image. Verify the Proxmox SSH connection under **Server** to make the ISO available for inspection. ISO builds use Proxmox's default firmware (SeaBIOS), machine type and display. They default to DHCP, 2 vCPUs, 4 GiB RAM and a 32 GiB system disk.

Enter a GitHub username to import that account's public SSH keys, paste one public key, or leave both fields empty for dashboard-managed access. Check the key fingerprints under **Installation details** in the review. The installer uses Ubuntu's virtual kernel, enables key-only OpenSSH and installs Python and the QEMU Guest Agent. The verified Proxmate SSH login makes the machine available to Operations.

For a working Ubuntu VM or template clone, tick **Allow password sign-in** under **Machine details**. After setup, use the Ubuntu account and password on the Proxmox console or at the SSH prompt. Proxmate continues to use its automation key. Set a password when building or cloning the VM that will use it.

Under **Software**, choose common Ubuntu packages, enter additional APT package names, or select Linux scripts from the library. The firewall and updates start with Ubuntu defaults. You can enable UFW with TCP 22 allowed, disable UFW, or choose automatic security updates or manual updates after installation. The review asks for separate confirmation before disabling the firewall or selecting manual updates.

Follow install and SSH verification in the job log. Use **Open console** for Subiquity progress or **Download Ubuntu autoinstall YAML** for a reusable copy of the installer configuration. The copy has an empty key list and a placeholder for the system disk serial. Check the disk path in both the early validation command and the storage layout when adapting the YAML.

A template runs its selected software, clears the build's SSH keys and guest identity, and shuts down with a Proxmox cloud-init drive. Select **Clone** under **Create → Available templates**, or choose **Ubuntu template** under **Install from**. Choose a full copy or a linked copy. Clones keep the template's boot firmware. Copies of older UEFI templates with the default display use VirtIO-GPU for a readable console. On the first boot Proxmate verifies the new hostname and SSH login before applying selected software.

After connecting, **Operations** lists the discovered VMs across the cluster. Search by name, node or VM ID, or filter by power state. While the inventory is open, Proxmate refreshes cluster resources about once a minute. **Create** offers Windows Server and Ubuntu wizards for working VMs and reusable templates. On a multi-node cluster, the wizard prefers the node reached through the verified SSH connection.

1. Choose **Working machines** or **A reusable template**, then enter the name and quantity. When cloning a Windows machine, choose a stopped, Sysprep-generalized Windows Server template with the QEMU guest agent installed. Set the new Administrator password. The **Clone** action under **Create → Available templates** also opens this wizard for Windows guests. A full copy is independent. A linked copy uses its template’s base disks. Proxmate applies the chosen name inside Windows on first boot.
2. For a fresh Windows installation, pick an ISO and a named edition. **Browse all media** includes unusually named images. Proxmate inspects the actual WIM or ESD for editions. Ubuntu uses an official cloud image and a generated SSH login key saved in the credential manager.
3. Under **Data disks**, add up to three new Windows volumes. Choose a size, label and drive letter. **Automatic** assigns a free letter. ISO builds reserve D:, E: and F: for the installer, setup and driver DVDs. The first selectable data-disk letter is G:. Template clones offer D: through Z: and check the guest for optical-drive conflicts before preparing disks. Once WinRM is verified, Proxmate matches each newly allocated disk by serial and size before preparing it as NTFS. An occupied letter, existing partition or ambiguous disk stops the job.
4. In **Software**, choose any Windows settings, common programs, server features or saved scripts, then review and create the machines. **Customisations** includes Remote Desktop, OpenSSH and desktop preferences. **Select all** selects the compatible settings in that group. Expand **Updates, security and console sign-in** for its own **Select all** and **Clear** buttons. Switch between categories to choose packages, server features and scripts. Leave the boxes clear for the image’s defaults. Common programs include Chocolatey automatically. For other packages, tick **Set up Chocolatey**, then enter their IDs. **Browse Chocolatey packages** opens the catalogue in a new tab. In **Script library**, choose **Add input** under **Ansible inputs**. For example, enter `share_name` as the variable name, `Share name` as the display label, and `Lab` as the optional default. Refer to the value as `{{ proxmate_inputs.share_name }}` in the YAML. Review freezes the script and inputs for this run.
5. Open the job log for progress, the console link and answer-file downloads. **Download reusable answer file** provides a ZIP containing the XML with password and key placeholders, the companion bootstrap script when present and a build manifest. **Download original with credentials** exports the exact generated files, including the setup password. The archive survives later build failures. Builds that predate this feature have no archived files.

When you select **Continue** on Software, the wizard shows the current review step. You can retry an unsuccessful review with your selections still filled in. A review creates no VMs. After **Create**, the dashboard shows notifications when work queues, starts, finishes or fails. You can switch pages while it runs. **View log** opens the details for an individual build.

Windows remote management is configured without an interactive or automatic login. During setup the helper configures HTTPS WinRM first, independently of the guest-agent installer. A protected SYSTEM task retries at startup and at one-minute intervals (up to 60 attempts); MSI installation waits until Windows Setup finishes. Working WinRM certificates are reused across retries. Proxmate still waits for guest-agent discovery of the IP/public certificate and a successful authenticated Ansible check before applying software or declaring the build ready. Local diagnostics are in `C:\ProgramData\Proxmate\bootstrap.log` and `guest-agent-install.log`, restricted to administrators and SYSTEM. Existing installations need an explicit repair or rebuild to receive a changed helper.

For non-evaluation Windows Server 2019, 2022 and 2025 Standard/Datacenter, the simple wizard selects a [public Microsoft KMS client setup key](https://learn.microsoft.com/en-us/windows-server/get-started/kms-client-activation-keys) from the ISO's build and edition metadata. It is added only to the Windows Setup pass to avoid the initial key prompt. It is not a licence or retail activation key: activation requires your organisation's KMS or another appropriate licensed activation method. Evaluation and unknown editions receive no guessed key. This applies to newly generated answer media; an installation already at the key prompt needs manual input.

The Windows path discovers a VirtIO guest-tools ISO already on Proxmox; it never downloads or executes unreviewed host tools. If guest tools are missing, add a current VirtIO ISO once. ISO inspection and Ubuntu image preparation require the verified SSH connection on **Server**. When starting a newly created Windows VM with answer media, Proxmate sends space keys during the first 20 seconds to catch the DVD boot prompt. It checks the new VM identity and attached answer media before sending input, and gives the disk boot priority for installation reboots. Slow boots or console-permission failures can still require manual input: press Enter for Boot Manager, select the Windows DVD, then immediately press a key. The job waits for installation/readiness for up to an hour and logs progress about once a minute. Guest setup waits release the worker between checks. Other machines can build or run jobs during installation. Removing or replacing the new VM ends its setup wait with a failure. Internet/package repositories and DHCP must be working inside the guest. Ubuntu uses generated NoCloud ISO media in existing ISO storage rather than modifying the host's storage configuration.

Choosing **template** runs the selected software first, generalizes the new guest, shuts it down and converts it. Windows uses Sysprep, removes generated answer media and its saved WinRM certificate, and schedules a fresh certificate on clone startup. Ubuntu cleans cloud-init/machine identity and SSH host keys and replaces setup media with a normal Proxmox cloud-init drive. The guided Windows clone workflow supplies a new name and password and verifies a clone-specific marker before running software. OOBE or licensing can still require console interaction. Failures retain the VM for inspection and prevent automatic conversion. A Windows Server ISO install has completed in the lab. Full Sysprep and clone validation on disposable guests remains open. See the [Windows acceptance checklist](docs/windows-workflows.md#disposable-guest-acceptance).

Use **Refresh connection** on a VM row or in a failed script job to discover its current guest-agent addresses. Choose **Test and save** to verify the login at the selected address, then start a new run for the affected VM.

For Windows VMs prepared by Proxmate, **Test and save** can verify a changed WinRM certificate through Proxmox and save it after the login succeeds. This uses the guest agent and either verified Proxmox HTTPS or the server’s saved SSH fingerprint. When a VM ID is reused, the dialog suggests the login recorded for the current Windows build if available. If Proxmate cannot identify the login, choose one for the current VM. Custom Windows listeners can use an issuing CA in **Manual settings**.

In **Operations**, select one or more VMs and choose **Run script**, **Power on** or **Graceful shutdown**. For a script run, choose a compatible library script or paste a **One-off script**. Library shortcuts preselect the script. The runner shows declared Ansible inputs and their defaults. Pasted Ansible task lists can reference `{{ proxmate_inputs.name }}` to prompt for a value. Pasted PowerShell and Linux shell scripts use the same syntax checks as the library. One-off source is kept with the reviewed run and job history. Each VM uses its own saved login. For machines missing a connection, choose a compatible credential or enter a new login. A successful review saves the discovered connections for those machines. Check the targets, credentials and inputs before starting the job.

For Windows, open the machine and choose **Check WinRM setup** to inspect its service and listener. Review the management client IP or subnet before enabling HTTPS on port 5986. If the guest agent is unavailable, the same dialog provides elevated console commands. After setup, test the guest login. The inventory shows the time of the last successful Windows login recorded for that VM ID. Existing Linux guests need SSH enabled. Without an agent or with ambiguous addresses/untrusted certificates, use the machine’s **Guest connection** or **Manual settings**; certificate verification is never disabled automatically.

### Open a machine

Select a machine name to see its resources, guest addresses and interfaces, latest visible backup for that VM ID, and recent matching jobs. **Open console** opens the selected guest on your Proxmox server. Use **Copy IP** or open a job to read its log.

On a running guest with the QEMU Guest Agent, choose **Connect this machine**. Pick an address if several are reported, then select a saved login or enter a new one. **Test and save** checks SSH on Linux or HTTPS WinRM on Windows. A successful test saves the login and shows when this machine’s current connection was verified. Failed tests leave the saved connection intact. **Manual settings** supports custom ports, SSH fingerprints and WinRM certificate trust. **Retest login** checks the saved settings after a change.

## Advanced provisioning

The original detailed wizard and management tools remain under **Advanced → Full dashboard**, for other distributions and specialist settings.

Choose **Build a machine** and follow Source, Guest settings, Hardware & network, then Review. Planning has no infrastructure side effects. Creation requires confirmation and refuses existing VM IDs.

The wizard supports LAN HTTP access and does not depend on `crypto.randomUUID`. After upgrading, reload existing browser tabs; if an old `crypto.randomUUID is not a function` error persists, force-refresh to replace the cached script.

- **Windows ISO:** choose local media or an existing Proxmox ISO. Select edition image index, display language, region, keyboard, timezone, computer name, optional administrator password and RDP. Autounattend media configures the new GPT disk, UEFI/TPM and optional RDP with NLA. Confirm the edition index with DISM; the display language must exist in the ISO. Automatic first-boot input is best effort; setup may still require a DVD boot key, activation or account prompts. Preparation success does not prove Windows installation completed.
- **Linux cloud images:** Ubuntu 22.04/24.04/26.04, Fedora Cloud 44 and Rocky Linux 9.8. Downloads use official sources and published SHA256 checksums. Select login user/public SSH key, hardware, DHCP or static IPv4, gateway and DNS. Choose a VM or template. Profiles are x86-64; release URLs can eventually expire.
- **Linux ISO:** prepare an installer VM and finish the distribution's installer in the Proxmox console. Arbitrary Linux ISOs do not receive an unattended answer file.
- **Recipes:** save the reviewed build configuration and an ordered list of guest tasks. VM ID and Windows setup password are omitted. A credential reference suggests the later guest login. Choose Build to reopen the wizard, finish installation, configure the guest connection, then Apply tasks. Recipe tasks do not run automatically while an OS is installing.
- **Templates:** generalize, shut down, remove setup secrets, then convert. Windows requires Sysprep /generalize /oobe /shutdown. Finish setup detaches and deletes only that VM's Proxmate-generated answer ISO. Remove cached Windows answer files and rotate the setup password before sharing a template. Proxmate cannot verify generalization.
- **Clones:** full-clone an existing template to a fresh ID. Cloud-init user/key options apply only to compatible templates. New machines remain stopped unless explicitly selected to start.

The advanced wizard’s raw XML download and the job’s original-file download contain any supplied administrator password. Generated media and Windows caches can also contain it. The preview redacts the password and new queue payloads are encrypted. Never attach answer media to existing machines. The original ISO library is mounted read-only; uploads refuse existing filenames.

## Guest tasks and credentials

1. Add a named credential in **Credentials**. Windows supports username/password; Linux supports a password or unencrypted PEM/OpenSSH private key, plus an optional sudo password. A WinRM issuing CA certificate can be saved with a Windows credential.
2. Choose a VM's **Connection** and enter its guest address, platform, port and credential. Windows uses HTTPS WinRM (5986), NTLM and certificate verification by default. Linux uses SSH (22) with a mandatory independently verified SHA256 host fingerprint. Linux tasks use sudo by default and require Python. WinRM/SSH must already be configured.
3. Run **Guest readiness** to check power, guest-agent information, the configured TCP endpoint, authentication and Ansible runtime. No subnet scan is performed. An unavailable guest agent is reported separately. The reviewed plan shows the configured address and credential name. Guest-agent IP mismatches abort execution when addresses are available; guest endpoints resolving to Proxmox or loopback/link-local addresses are rejected. Without a guest agent, the address remains operator-supplied: verify it belongs to the selected VM.
4. In **Task library**, select tasks, enter package/service parameters, arrange their order and select VMs/groups. Optionally take a pre-task snapshot. Review the exact resolved targets, then run.

Built-ins cover Chocolatey installation/packages, RDP with NLA, separate Windows firewall disabling/enabling, Windows security/critical updates, Linux packages/updates and Windows/Linux services. Firewall disabling is never bundled with RDP. Updates do not automatically reboot. Chocolatey can require .NET 4.8 and Internet/package-repository access inside the guest.

Open **Script library** to start from Windows or Linux Ansible and PowerShell examples, or a Linux shell example. **Open example** opens an editable copy. The editor supports highlighting, line numbers, find and replace, indentation and undo. Use **Check syntax** (Ctrl/Cmd+Enter) while editing or **Save script** (Ctrl/Cmd+S) to check and save. Diagnostics include a line and column when the parser can locate the error.

In **Ansible inputs**, choose **Text**, **Dropdown**, **Yes / no**, or **Free drive letter**. Enter the allowed dropdown values separated by commas. A yes/no input becomes a boolean in the playbook. The built-in Windows and Linux service tasks use a dropdown for their action. Operations disables letters used by any selected Windows VM. During creation the picker shows known setup and disk reservations, and the worker checks the running guest immediately before executing the script.

Proxmate checks Ansible YAML and supported guest modules, uses the PowerShell language parser, and checks POSIX shell syntax with `sh -n`. Runtime dependencies and guest permissions are checked during the actual run. Linux PowerShell scripts require `pwsh` on the target. Docker builds and serves the editor assets locally. For a source checkout, run `npm ci` and `npm run build:editor` to build the same assets.

Save custom PowerShell/shell scripts or YAML lists of Ansible guest tasks. Custom Ansible accepts one supported fully qualified guest module per task and an optional name; play-level hosts, delegation, connection overrides, includes and loops are rejected. Example:

```yaml
- name: Install curl
  ansible.builtin.package:
    name: curl
    state: present
```

Supported modules are listed in guest_tasks.py under validate_script. This is trusted administrator code, not a sandbox. Scripts and templating are powerful operations. Use the advanced **Ansible** page for complete playbooks/custom inventory. Targeting the hypervisor there requires a separate checkbox and does not use saved guest credentials.

Guest logs include task names and per-VM results. Arbitrary module output is withheld and no_log is applied to protect secrets. Failure stops remaining tasks on that VM; other reviewed VMs still receive their own results. No automatic retry/rollback occurs. Timeouts can leave remote work running: inspect before retrying.

Credential values are encrypted and write-only through the API. Blank secret fields on edit retain existing values. Rotation invalidates plans/schedules using the old revision; create a fresh reviewed plan. References prevent deleting credentials used by connections, groups, recipes or schedules.

## VM controls, snapshots and schedules

**Manage** offers start, graceful shutdown, confirmed force-stop, CPU/RAM changes, backups, snapshots and snapshot restore. CPU/RAM changes require stopped VMs and apply on next start; CPU topology is set to one socket. Graceful shutdown times out after 120 seconds without forcing power off. LXC guests are visible but actions target QEMU VMs.

Snapshots require storage/guest support. A failed pre-task snapshot prevents that VM's tasks. Snapshots are retained and named in job logs. Snapshot restore requires a stopped VM and separate destructive confirmation because it replaces current state. Backups use snapshot-mode vzdump with Zstandard. Use **Backup recovery** for archive restoration, isolated restore checks and reviewed retention.

Groups save explicit members and an optional credential override for runs selected through the group. Plans freeze exact targets, script versions and connection settings. Later edits cannot expand a plan; VM identity/disk changes invalidate execution.

From a reviewed task, backup, start, shutdown or readiness plan choose **Save as schedule**. Schedules default disabled. Set an interval of at least 15 minutes, a same-day UTC window, then explicitly enable. First run is at least one interval later, aligned to the next open window. Daylight-saving changes do not affect UTC schedules.

Schedules use the serialized job queue. Late/missed occurrences are skipped, including after restart. Runs do not overlap their own previous active occurrence. Schedules link to the latest per-VM job report; no external notifications are sent. The window is checked when queuing and before starting each VM; a late target is reported as skipped/failed without changes. An operation already running may finish after the window closes. To refresh targets/tasks/credentials, disable the old schedule and create a newly reviewed one.

## First-boot bootstrap

In the provisioning wizard, explicitly enable **Guest bootstrap** in Hardware & network. Preparation and authenticated readiness are separate outcomes.

- Linux cloud images use cloud-init **vendor data**, preserving generated users, SSH keys and networking. Choose an existing snippets-enabled storage whose snippets directory is available. The guest installs qemu-guest-agent, OpenSSH, Python and sudo, then writes a completion marker. The snippet remains referenced by templates/clones; do not remove it while needed. Proxmate does not enable storage content types automatically.
- Windows runs a helper from generated answer media during specialize. Select a trusted VirtIO ISO containing the x64 guest-agent MSI and matching signed vioserial driver for Windows 10/11 or Server 2019/2022/2025. The helper configures HTTPS WinRM first and retries guest-tool installation independently. New build plans pin the MSI SHA-256 from the selected ISO and recheck it before creation and inside the guest. An unsigned community MSI requires that matching pin; invalid signatures and changed bytes are rejected. Windows still enforces driver signatures. HTTP WinRM listeners and Basic/unencrypted authentication are disabled. The firewall rule permits only the chosen management client IP/CIDR. Use the actual LAN source address/subnet, not an unrelated Docker container subnet.
- Optional authentication verification requires VM purpose, Start enabled and a saved credential matching the installed login. After the completion marker appears, Proxmate retrieves the public SSH host key or public Windows TLS certificate through the authenticated Proxmox guest-agent API, checks the guest IP, and tests SSH/WinRM with verification enabled. Success saves the VM connection. Windows certificate trust is stored per connection, not overwritten for other VMs sharing a credential.

The worker saves a checkpoint after VM creation. While verification is pending, the job shows **waiting**. Work on the same VM and jobs with unspecified targets stay queued. After a service restart, Proxmate resumes the readiness check with its original deadline, VM identity and login revision. Package repositories, drivers, networking and account setup must work. Windows OOBE/licensing prompts may still require the console. Failed VMs remain for inspection. Windows certificates include the current computer name/IPv4 addresses and expire after one year; refresh bootstrap and connection trust after relevant identity/address changes.

For interactive Linux ISOs, use **Task library > Guest bootstrap helper** after installation. Downloading never executes it. Inspect the script and run elevated only inside the intended guest. The Windows helper also exports its public certificate to `C:\ProgramData\Proxmate\winrm.pem`, which can be entered in Connection settings. Downloaded answer XML alone does not include the helper: use the generated answer ISO flow or provide its matching script alongside it.

The guided Windows template workflow generalizes the guest, clears the cached source answers and bootstrap certificate, and points Windows Setup at the next clone’s DVD. The guided clone wizard supplies first-boot media and provisions a new guest certificate. The [Windows workflow guide](docs/windows-workflows.md) includes the live template-and-clone test and guidance for older templates. For manually cloned guests, run the standalone helper after configuring the guest’s name and network, then test the login.

## Backup recovery and retention

Open **Backup recovery**, choose an archive and supply a new VM ID/storage. Review and execution both reject occupied IDs. Restores request unique identities, no overwrite, no immediate start and disabled onboot. By default, all restored NICs are disconnected before any test boot. Original networking can be retained only with test boot off; inspect it before starting that restored VM yourself.

Optional boot checks start the disconnected copy, wait for guest-agent OS information, gracefully shut it down and retain it. No force-stop or automatic VM deletion occurs. A failed shutdown can leave a disconnected test VM running; inspect its report. Passthrough devices, host device paths, hooks, custom QEMU arguments and generated answer media are rejected by this workflow.

A successful Proxmox restore task proves restoration completed. A boot check additionally proves guest-agent OS responsiveness. Neither validates application/data consistency; test application recovery separately.

**Scheduled restore checks** select the newest backup for one source VM/storage and an unused ID in a reserved range. Policies default disabled and skip missed/overlapping occurrences. Every test VM is disconnected and retained. A full range stops further checks rather than deleting or reusing occupied IDs. Inspect and clean up copies through Proxmox.

**Retention review** preserves at least the newest selected count and all protected archives, proposing only archives older than the configured age. Confirmation lists exact archive IDs; metadata, protection and eligibility are checked again before each deletion. The deletion list never expands after review. Saved policies default to preview-only. Recurring deletion needs explicit auto-delete and risk confirmation, scoped to one node/storage/source VM. Archive identity uses Proxmox metadata, not an independent full-file checksum.

## Versioned templates

In **Template versions**, create a family, then build a replacement from a saved recipe into a fresh ID. Provenance records configuration, source image/checksum when available, and task history without setup passwords. Windows ISO provenance identifies selected media; it does not claim a full ISO checksum.

Task definitions are frozen for the reviewed build. They run automatically only after enabled bootstrap authentication verification on a started candidate. Otherwise they remain marked as planned: finish installation, configure its connection and apply tasks separately.

Generalize and shut down, remove setup media/secrets, then **Register version** with a unique label. A stopped VM is converted to a template and onboot disabled. Existing templates can be adopted with unavailable historical provenance clearly labelled. Build provenance follows machine identity, not just a reusable VM ID.

**Test clone** creates a full copy in a fresh ID, disconnects it, boot-checks through the guest agent and shuts it down. **Promote** requires successful evidence tied to unchanged registered configuration. Promotion updates the preferred pointer; previous versions and test clones remain. **Clone preferred** checks registered identity before cloning. Configuration changes require registering/testing a new version.

## Lifecycle API

The authenticated API adds these routes; OpenAPI contains full request schemas:

| Route | Purpose |
| --- | --- |
| `GET /api/lifecycle/state` | Families, versions, provenance, policies and recovery reports |
| `POST /api/lifecycle/plan` | Review restore, retention, version build/register/test/promote/clone |
| `POST /api/lifecycle/run` | Queue the exact reviewed plan once |
| `POST /api/lifecycle/families` | Create a template family |
| `POST /api/lifecycle/policies` | Save disabled-by-default backup policies |
| `POST /api/bootstrap/helper` | Download a guest-side helper without executing it |

Lifecycle plans expire after 30 minutes. Submission requires `plan_id` and `confirmed:true`; deletion/custom scripts also require `risk_confirmed:true`. Repeating the plan ID returns the same job. Disable both management schedules and backup policies before upgrades.

API contracts were checked against the [Proxmox QEMU API source](https://github.com/proxmox/qemu-server/blob/master/src/PVE/API2/Qemu.pm), [guest-agent API source](https://github.com/proxmox/qemu-server/blob/master/src/PVE/API2/Qemu/Agent.pm), and [Microsoft specialize command documentation](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-deployment-runsynchronous-runsynchronouscommand-path).

## API

- [Windows provisioning guide for AI agents](http://localhost:8088/static/llms.txt) ([repository copy](static/llms.txt))
- Human-readable reference: http://localhost:8088/api/docs
- OpenAPI 3.0 JSON: http://localhost:8088/api/openapi.json
- Inventory: `GET /api/state`
- Job submission: `POST /api/jobs`
- Job history/status/log: `GET /api/jobs` and `GET /api/jobs/{id}`

Retrieve the generated bearer token locally (this command prints a secret):

```sh
docker compose exec proxmate cat /data/api-token
```

Use `Authorization: Bearer TOKEN`. Read `GET /api/server` and send its `id` as `X-Proxmate-Server` on host-bound POSTs: jobs, management except credentials/tasks, and lifecycle plan/run/families/policies. Missing or obsolete IDs return HTTP 409; reload and review the current server before retrying. This protects against stale tabs and scripts submitting old VM IDs to a replacement host. The Server API provides `/api/server/test`, `/api/server/connect` and `/api/server/disconnect`; include `expected_id` from the GET response (null for initial setup). Connection requests use `url`, `username`, write-only `password`, `verify_tls`, `ssh_user`, `ssh_fingerprint` and optional `replace_confirmed`.

Send JSON and optionally an `Idempotency-Key` on submissions. The same key and payload returns the original job; a changed payload returns HTTP 409. Use a new key after replacing the server. HTTP 202 means queued, not successful. Poll the job until its status becomes `succeeded`, `failed` or `interrupted`.

```sh
curl http://localhost:8088/api/jobs \
  -H "Authorization: Bearer $PROXMATE_TOKEN" \
  -H "X-Proxmate-Server: $PROXMATE_SERVER_ID" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: backup-9002-2026-11-14" \
  -d '{"action":"backup","node":"pve","vmid":9002,"backup_storage":"local"}'
```

Supported legacy/job actions: `provision`, `finish-setup`, `linux-template`, `installer`, `clone`, `start`, `convert-template`, `backup`, `upload-iso`, `ansible`. Hypervisor Ansible requires `host_confirmed: true`. OpenAPI includes example payloads. Browser authentication uses a session cookie plus CSRF token; scripts should use the bearer token.

Additional routes (authenticated; JSON for writes):

| Route | Purpose |
| --- | --- |
| GET /api/catalog | Linux profiles and Windows locale/timezone options |
| POST /api/provision/plan | Pure validation and redacted answer preview |
| POST /api/windows/answer-file | Explicit answer XML download |
| GET/POST /api/manage/{kind} | List/create/update credentials, connections, tasks, groups, recipes or schedules |
| POST /api/manage/{kind}/{id}/delete | Delete unreferenced saved configuration |
| POST /api/manage/plan | Resolve and review exact guest-management targets |
| POST /api/manage/run | Queue a reviewed plan once |
| GET /api/manage/snapshots/{node}/{vmid} | List snapshots |

For example, POST /api/manage/plan with:

```json
{"operation":"shutdown","targets":[{"node":"pve","vmid":201}]}
```

Then POST /api/manage/run with the returned plan ID and confirmed=true. Risky plans also require risk_confirmed=true. Plans expire after 30 minutes. Repeating the plan ID returns the same job; retrying failed work requires a fresh reviewed plan. Saved scripts, groups and connections are frozen; credential rotation invalidates the reviewed revision. Full request schemas are in OpenAPI.

## Persistence and recovery

The `proxmate-data` Docker volume contains jobs, encrypted records, schedules/policies, session/encryption key and API token. Back up the database and encryption key together; losing the key loses credential access. Keep one application process/replica. Disable schedules and backup policies and wait for active work before upgrading. Do not remove the volume during normal upgrades. Jobs are serialized. A checkpointed guest-readiness wait survives a restart. Restarting marks queued/running jobs `interrupted`; they are never replayed automatically. Remote operations may still be running or have created partial VMs. Inspect Proxmox before retrying. Failed resources are retained; the application exposes no VM deletion operation.

New job payloads and management records are encrypted at rest. Historical jobs predating encryption remain in their original format. Encryption does not protect against a compromised administrator or running container. The advanced Ansible page retains output and can expose secrets printed by custom playbooks; guest-library runs suppress output. Protect the volume and back it up before upgrades.

```sh
docker compose logs --tail=100
docker compose ps
docker compose stop
docker compose up -d --build
```

To diagnose a slow review remotely, use the request ID shown with the wizard error. On the Docker host, run the following command and search the output for that ID.

```sh
docker compose logs --since=30m proxmate
```

JSON events show the review stage, elapsed milliseconds, media cache hits and remote block reads. Slow or unsuccessful Proxmox calls report the operation and HTTP status where available. The `plan.ready` event links the request to a plan ID. The `job.queued` event links that plan to a job ID for following VM creation, readiness waits, software and completion. Container diagnostics use operation names, identifiers and timings. Request bodies, credentials, answer files and raw guest or upstream output are excluded.


## Security and scope

Everyone with the dashboard password or API token is an administrator. Do not publish this directly to the Internet. Use an HTTPS reverse proxy for shared access and set `COOKIE_SECURE=true` in the container environment. The initial Compose setup serves HTTP on a trusted LAN. Proxmox API credentials are server-side only; SSH operations pin the host key. The current SSH implementation operates only on the configured host, not arbitrary nodes in a cluster. API-only operations can target other nodes.

## Development checks

```sh
python -m pip install -r requirements.txt pytest
python -m pytest -q
npm ci
npm test
node --check static/app.js
node --check static/wizard.js
node --check static/manage.js
node --check static/lifecycle.js
docker compose build
```

Backend tests cover authentication, secrets, frozen plans, snapshot failures, bulk partial failures, schedules and provisioning. DOM tests cover navigation, task review/submission and recipe password exclusion. They do not contact real guests. The built image can be checked with networking disabled:

```sh
docker run --rm --network none --entrypoint python -v C:/Code/proxmate/tests:/tests:ro proxmate-proxmate /tests/container_check.py
```

Adjust the bind mount on another machine. This checks authentication, static routes and the installed Ansible collections without executing guest tasks. Full visual/mobile review and actual WinRM/SSH/installer execution still need validation on the intended server.

Inspired by [Creating Proxmox Templates with Ansible](https://www.rootisgod.com/2024/Creating-Proxmox-Templates-with-Ansible/). Template creation intentionally refuses VM-ID collisions rather than deleting existing guests.

Official references: [Ansible WinRM](https://docs.ansible.com/projects/ansible/latest/os_guide/windows_winrm.html), [Chocolatey module](https://docs.ansible.com/projects/ansible/latest/collections/chocolatey/chocolatey/win_chocolatey_module.html), [Proxmox guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf).
