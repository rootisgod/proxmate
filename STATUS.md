# Proxmate status — 2026-11-22

The Docker app runs at http://localhost:8088. API reference: /api/docs; OpenAPI: /api/openapi.json. The dashboard password is in an ignored local secret file; saved Proxmox and guest credentials are encrypted in the data volume. Never copy secret values into this document.

## Lab deployment cleanup preview · 2026-11-22

Open a run in **Labs → Deployments** and choose **Preview cleanup**. The read-only preview shows each role’s current node, VM name and power state. It explains why the VM is a cleanup candidate or protected. **Refresh preview** checks Proxmox again, and opening a console rechecks the guest identity. [Dashboard Help](static/help.html#lab-cleanup)

The live dashboard on port 8088 passed the health, API and Help checks. Its deployment history was empty at verification. In an isolated run of the built image, only the lab-created web VM qualified as a candidate. The existing management VM and its shared login stayed protected. After a replacement guest reused the web VM’s ID, a refresh withdrew the candidate and the old console link returned HTTP 400. All 458 backend and 76 dashboard tests passed.

## Dashboard backup and restore · 2026-11-22

**Resources → Backup & restore** is live on port 8088. Both encrypted export and read-only preview passed against the running dashboard. The full preview included 108 historical jobs, three credentials, two labs and two custom scripts. The portable file contained four definitions and reported no omitted builds.

Full replacement and selective import passed on disposable installations with separate volumes and encryption keys. The release-install check now exercises both workflows. Verification included 455 backend and 75 dashboard tests, an offline container check and rollback after a failed write. [Backup specification and operator guide](docs/dashboard-backups.md)

## Portable lab packs

**Create → Lab packs → Create lab** is live. It supports new machines, blueprints based on existing VMs, and ordered scripts on an explicitly mapped existing guest. The `Tools rollout` example is saved on the dashboard. Its live review proposed a new Ubuntu VM on `nuc` followed by a curl package task on the existing `ubuntu-pm` VM. Reading `win-dctst` as a blueprint recovered its guided installation settings and a fresh DSRM password prompt. These checks stopped at review and the job and run histories stayed unchanged.

Version 1 packs continue to create new machines. Version 2 adds in-place task roles with separate deployment approval. Mixed-lab worker tests cover atomic confirmation, stale target identities, changed disks and credentials, guest readiness and failed prerequisites. The browser check saved all three choices in one pack and exercised mapping and review on isolated inventory.

The saved **Ubuntu tools lab** example was imported, downloaded byte-for-byte and reviewed against `nuc`. It proposed one Ubuntu cloud VM with curl and an embedded Ansible directory task.

An isolated two-dashboard test transferred a Windows build across different databases and storage catalogs after its source script was edited. A browser test exercised ZIP upload, review, confirmation and saved-pack reuse. Queue tests cover repeated confirmation, partial failure, independent branches and restart during guest readiness. The Ubuntu example's directory task ran twice in a disposable container and reported a change only on the first pass. The [pack specification](static/lab-packs.md) describes both versions.

## Windows domain controller verification

On `nuc`, build `d3c270f4409dc81b57cd000211fd6630` completed promotion and the next customization before its final firewall task failed. Authenticated WinRM checks on the original guest verified the forest root, domain controller role, running AD DS, DNS and Netlogon services, SYSVOL and NETLOGON shares, and both public desktop shortcuts. The NetSecurity cmdlet rejected PowerShell Boolean arguments for its `Enabled` enum. Both corrected enum names passed a live `-WhatIf` check. The job checkpoint retained all 11 completed tasks.

The operator chose to leave all three firewall profiles enabled. The failed final step was not retried.

## Ubuntu Server LTS installer qualification

The amd64 live-server ISOs on `nuc` matched Canonical’s published SHA-256 checksums for [22.04.5](https://releases.ubuntu.com/22.04.5/SHA256SUMS), [24.04.5](https://releases.ubuntu.com/24.04.5/SHA256SUMS) and [26.04.1](https://releases.ubuntu.com/26.04.1/SHA256SUMS). The wizard reads the release and installation sources from each selected ISO. Standard and Minimized use the same vendor disc.

| Ubuntu Server | Standard VM job | Minimized template job | Full clone jobs | Ansible Operations job |
| --- | --- | --- | --- | --- |
| 22.04.5 | `7f7ee7c23f553a1bada732b9ce61a5e3` | `e916aefcca93e4a663cd36a1af5e08b5` | `72668f3bc18eb6aebd741d10a7e559cb`, `12f3b4f07a192597234f22bff16e0af8` | `564ccf5dd52f4877a512e6c63e491005` |
| 24.04.5 | `4f59572c709d2fc1dd3bce4495c0f44d` | `a3bc28194c6e7a2eca31df5458c93b7d` | `c171137c561039d90195fe916bbba37d`, `60f9e51279317a60f95ff8081228de27` | `216d3da16b6f42ca919bea7bc9d13a68` |
| 26.04.1 | `a40520e9694da7169f8b647069c34891` | `23aae56454623675da3ffbcfc96966bc` | `c3ebeaeed057fe4b2171d71f35637943`, `bda5eef81be953359c34f98f50f28879` | `b8ca456fc8344c51aff77e23a8b8c7a7` |

Each row records a completed unattended install of both editions and a targeted Operations run through the dashboard. Independent SSH checks read the release, installer source, hostname and machine ID inside the guests. The Standard VM accepted separate key and password logins. The Minimized template stayed stopped while two full clones booted with separate disks, machine IDs, SSH host keys and authorized keys. Both editions ran the QEMU guest agent, `sudo` and `jq`. Ansible created a directory on one clone and a check on the other confirmed its absence. Both ISO jobs have downloadable autoinstall YAML with machine-specific values removed. [Repeat the live test](README.md#ubuntu-server-lts-iso)

All 12 Ubuntu test guests and their 21 disk volumes were removed after verification. One 26.04.1 clone timed out on its first graceful shutdown during cleanup and stopped on retry. The 15 dashboard job histories, six reusable Ubuntu answers, original ISOs and all 50 pre-existing guest identities were verified after cleanup. The dashboard queue was idle. Validation also passed 303 backend tests, 54 UI tests and the network-isolated container checks.

## Debian 13.7 netinst on nuc

Both unattended installs used `SSD:iso/debian-13.7.0-amd64-netinst.iso` on the live dashboard. Neither required console input. Each installation job has a downloadable preseed with placeholders for machine-specific values.

| Guest | Dashboard job | Verified result |
| --- | --- | --- |
| VM 160, `dbv-6262f5` | `37c3ca1e82f1e16341458789490a07f9` | Booted from its system disk after installer removal. Independent SSH key and password logins verified the hostname, Debian 13, passwordless sudo and `jq`. |
| Template 161, `dbt-6262f5` | `73fb3e3c6c2c771fffae2a027d383aca` | Installed `jq`, sealed successfully and remained stopped with the same reviewed configuration after cloning. |
| Full clones 162 and 163 | `ff7e516c87943f99640ea0574808a1dc`, `228bed56ce4625c656741d530a8c8809` | Both inherited `jq` and had independent disks, hostnames, OS machine IDs and SSH host keys. Their authorized keys excluded the template's login key. Operations job `1f0e9e84b6d3451693e00a385649dc71` created a directory on 162. A separate SSH check confirmed it was absent on 163. |

The initial cleanup removed the two temporary clones and 29 earlier test guests from `nuc` (12 Ubuntu, 7 Debian, 7 Rocky and 3 Windows). Thirteen of the earlier guests had been running. The inventory confirmed removal of their 63 disk volumes and 8 generated installer images.

After Ubuntu qualification, the operator approved removing the retained Debian VM and template. VM 160 shut down gracefully. Both guests and their three disk volumes were removed after checking build identities and storage dependencies. The dashboard still serves both job histories and preseed downloads. The original Debian netinst ISO and all 48 other guest identities were verified after cleanup.

## Rocky Linux 10 guided builds

### Rocky 10.3 Boot and Minimal

The copies on `nuc` matched the publisher's SHA-256 for both [Boot](https://download.rockylinux.org/pub/rocky/10.3/isos/x86_64/Rocky-10.3-x86_64-boot.iso.CHECKSUM) and [Minimal](https://download.rockylinux.org/pub/rocky/10.3/isos/x86_64/Rocky-10.3-x86_64-minimal.iso.CHECKSUM). Minimal's local repository includes the minimal environment but omits cloud-init and the QEMU guest agent. Its Kickstart uses the disc plus version-matched Rocky HTTPS mirrors. Boot carries Anaconda and requires an online installation tree. All three Rocky ISO variants tested here share the same volume label.

The initial Boot test used Rocky's direct HTTPS repository. VM 116 (`f7b17cbff62e4628b92539e557ee37cd`) and template 117 (`031d8979cfaaa9e2ef91d106854acb81`) completed without console input. Full clones 120 and 123 had independent guest identities, inherited `jq`, and passed the scoped Ansible check (`27fa45ba723a46e3a36f3937eafef8e1`). Throughput varied substantially on the first install. The current Kickstart uses Rocky's regional mirror list.

| Installer | Working VM job | Template job | Full clone jobs | Operations job |
| --- | --- | --- | --- | --- |
| Minimal | 126 · `a0ab15a250029303a539d8a2c7033664` | 127 · `02907e1689449544d997517fe028a8b0` | 128 · `53caeab35a9a21876ba4b19fd1a4112d` and 130 · `05c81524de50ebd5dbf8ffd0508e7bf1` | `5a8ac3d290f34df3b3f090d505cef4f0` |
| Boot | 134 · `cb8e7ea6c53b1f993127d4a7838fcae2` | 140 · `84793d5c75b8a2848bf095e35fb05ee4` | 141 · `01fd8e96b6990e211b7a08b873b2fb6d` and 142 · `200fe274d0210b50625ffbec28b9451e` | `9f25af2b040e4462b828668d51959615` |

The regional-mirror runs completed unattended. The Minimal VM and template jobs took 5 min 51 s and 5 min 55 s. The Boot jobs took 7 min 21 s and 7 min 37 s. SSH checks verified Rocky 10.3, key and password logins on the working VMs, sudo, `jq`, the guest agent, SELinux enforcing and SSH through firewalld. Each pair of full clones had independent disks, machine IDs and SSH host keys. An Ansible task created its directory only on the selected clone. Both ISO jobs for each variant retained their reusable Kickstart files.

After verification, all 12 Rocky test guests, including the templates and comparison guests, and their 21 disk volumes were removed at the operator's request. The dashboard histories and original Rocky ISOs were retained. VM IDs may be reused. All 50 pre-existing guest identities were accounted for at the end of Rocky qualification. Validation included 300 backend tests, 53 UI tests, RHEL 10 Kickstart parsing for all three ISO variants and network-isolated container checks.

### Rocky 10.3 DVD

The live dashboard completed an unattended Rocky Linux 10.3 DVD install, template preparation and two full clones on `nuc` using `SSD:iso/Rocky-10.3-x86_64-dvd1.iso`. In **Create**, select **Rocky Linux** for a working VM or reusable template. ISO jobs offer a downloadable Kickstart with public-key and disk-serial placeholders. [Acceptance test instructions](README.md#rocky-linux-10)

| VM | Dashboard job | Verified result |
| --- | --- | --- |
| 156 · `rkv-e12460` | `f96b9057e0f94d82c85f3a48524b3c5b` | Installed unattended, ran the DNF package task for `jq` and accepted independent SSH key and password logins. |
| 157 · `rkt-e12460` | `b17a1b86b2342644477d16304f2239d8` | Sealed through verified SSH and remained a stopped template with its reviewed configuration after cloning. |
| 158 · `rka-e12460` | `04ac50b97295f23d74c6f3de38f58d00` | Inherited `jq`. Parameterized Operations job `c0f3746fad0942cfaed164ab8f998595` created `/var/tmp/pm-e12460` using the saved login. |
| 159 · `rkb-e12460` | `93f06aa6f5c6407fac8870818168439e` | Booted with its own login keys and inherited `jq`. The directory created on 158 was absent. |

The test verified distinct hostnames, OS machine IDs, SSH host keys and independent storage volumes. Both clones had SELinux enforcing, SSH allowed by firewalld, and the source template's Anaconda answer files removed. Each guest published its public SSH key for the confined QEMU agent to read. The hypervisor-reported key matched the dashboard pin. Guest-agent command execution and file writes remained disabled.

Earlier attempts exposed Anaconda's RAM-backed zram swap and Rocky's guest-agent and SELinux file-access restrictions. Diagnostic VMs 143, 144 and 155 were stopped and retained at the time, then removed during the Debian netinst cleanup. The final DVD run required no console intervention. Verification passed 298 Python tests, 53 UI tests, RHEL 10 Kickstart parsing and isolated container checks. Rocky 9 remains outside this Rocky 10 qualification.

## Debian 13 guided builds

The live dashboard on `nuc` completed a working VM, a template and two full clones using `SSD:iso/debian-13.7.0-amd64-DVD-1.iso`. In **Create**, choose **Debian** for a virtual machine or reusable template. Each ISO job includes a downloadable preseed with placeholders for the public keys and disk serial. See the [acceptance-test instructions](README.md#debian-13).

| VM | Dashboard job | Live result |
| --- | --- | --- |
| 145 · `dbv-5d2b4e` | `8448ef084913ca07fd291883a4563863` | Installed unattended. An independent SSH client verified both password and public-key sign-in, the hostname, `sudo` and `jq`. |
| 149 · `dbt-e00474` | `6bd05502a32c07b24ce3c039b01b69e4` | Installed `jq`, removed installer media and login keys, and shut down as a reusable template. Its configuration remained the same after both clones. |
| 153 · `dba-e00474` | `be516c93ddb0910c89e1188e371033ef` | Inherited `jq`. Operations job `7081c885e222432b90e8506ba0350f02` used the saved login and a caller-supplied Ansible input to create `/var/tmp/pm-e00474`. |
| 154 · `dbb-e00474` | `fa792e17ecd62da2c9f57dbadbd78ecb` | Inherited `jq`, accepted its own login keys and had no directory from the task on 153. |

Both clones had distinct hostnames, SSH host keys and OS machine IDs. The D-Bus ID matched the OS ID within each guest. Node storage metadata confirmed independent disk volumes. The test exposed two installer issues with generated boot media and public-key handling, plus a separate D-Bus machine ID that had survived template preparation. All three fixes passed unattended on the real DVD. The subsequent [netinst test](#debian-137-netinst-on-nuc) exercised the same workflows.

Diagnostic guests 143 and 144 and their disks were removed after verification. Their dashboard histories and the original Debian ISO were retained. Clones 147 and 148 inherited the same OS machine ID from the earlier template 146. Template 149 verified the correction. The verification suite passed 289 Python tests, 52 UI tests and the network-isolated container checks.

## Unattended Windows templates and full clones

The live dashboard completed a Windows Server 2025 Standard (Desktop Experience) ISO-to-template build and two full clones on `nuc`. The [repeatable acceptance test](docs/windows-workflows.md#run-the-template-and-clone-test) submitted all three builds through the dashboard and checked the resulting guests over certificate-verified WinRM. The clean run required no console input.

| VM | Dashboard job | Verified result |
| --- | --- | --- |
| 150 · `pwt-a6b62473` | `aa4fa0c83d78ee47688daa5e843580f5` | Installed 7-Zip, completed Sysprep, and remained a stopped template with the same configuration after both clones. |
| 151 · `pwa-a6b62473` | `15d84c43cff4ec120d734e6986cb6139` | Accepted its own login, inherited 7-Zip, formatted the 2 GiB NTFS volume `PMData` on R:, and created `C:\PmToolsa6b62473` from the example Ansible task's input. |
| 152 · `pwb-a6b62473` | `a94e4ceae30de734caacbf0dc3bee39b` | Accepted a separate login, inherited 7-Zip, and had only the Windows system disk. The first clone's folder was absent. |

Both clones finished Windows Welcome, reported the requested hostname and edition, used distinct HTTPS listener certificates, and released the setup DVD reference. The source template's cached answer was absent from the checked setup paths. Each job returned a reusable redacted answer archive. The node's storage metadata confirmed independent disk volumes for both clones.

The live tests reproduced stale cluster power status after shutdown, a drive-letter change between installation and cloning, an extra cached Windows answer containing the source password, and a guest-agent lock on the progress file. New template builds check power on the hosting node, clear cached answers around Sysprep and direct Windows Setup to the clone DVD by device path. Progress reporting tolerates a sharing violation while guest completion and authentication remain mandatory. The changes apply to newly sealed templates. Inspect older Windows templates before sharing them.

After the clean pass, diagnostic VMs 143–149, their disks and the remaining generated answer ISO were removed. All seven job histories and the original Windows and VirtIO installer ISOs were retained. The dashboard lists 150 as a stopped template and 151 and 152 as running VMs with verified connections. Validation passed with 282 Python tests, 51 UI tests, 18 Windows bootstrap tests, 11 disk-format cases with mocked storage, and the network-isolated container checks.

## Guided saved builds and guest task recovery

Create now lists saved Windows and Ubuntu builds. A guided review or job can supply the settings for a named build. The next review checks the source, allocates available IDs and asks for fresh login secrets. Activity offers a reviewed continuation from per-VM guest task checkpoints. Completed tasks stay complete and competing recovery reviews can claim the same remaining work only once.

Live verification on `nuc` used a saved build to create `pm-retry-qa` (142), a linked clone of `noble-base` (128). The first guest task succeeded and the second encountered an intentionally missing test service. After its transient unit was added to the test VM, the dashboard verified SSH and offered the remaining two tasks. Recovery job `68aaffe8d9c949a7a66020a851164d31` completed both on VM 142. The test VM was gracefully stopped and the temporary saved build removed. Historical Windows Server 2025 and Ubuntu 24.04 ISO jobs both produced fresh reviews.

## Ubuntu console and firmware

New Ubuntu ISO installs use the default Proxmox firmware, machine type and display. The generated BIOS boot disc carries the installer kernel and initial RAM disk so GRUB can start before the Linux installer discovers the original DVD. Guided clones of earlier UEFI templates preserve the firmware and select VirtIO-GPU when the template uses the default display.

On `nuc`, `ubuntu-pm` (133) displayed a readable login prompt with VirtIO-GPU and its original UEFI boot. It was returned to its stopped state. `noble-std` (140) completed from ISO under SeaBIOS, verified SSH and displayed a readable login prompt after reboot with the default display. The earlier failed BIOS test `noble-bios` (134) is retained stopped. 261 backend tests, 47 UI tests and the isolated image check passed.

## Ubuntu password sign-in

**Allow password sign-in** adds console and SSH password access to the selected Ubuntu account on a working VM or clone. The review describes the change, and Proxmate continues to use its SSH key for automation. A disposable linked clone on `nuc` accepted password-only SSH for its guest account and completed a separate Ansible run with its saved key. Browser review, 262 backend tests, 48 UI tests and the isolated image check passed.

## Ubuntu Server 24.04 release

The live dashboard supports Ubuntu Server 24.04 ISO autoinstall for working VMs and reusable templates. It discovers media, reviews optional GitHub or pasted SSH keys by fingerprint, and applies selected settings, packages and Ansible scripts after authenticated SSH. The job log offers reusable NoCloud YAML. The installer uses Ubuntu's virtual kernel. The firewall and background update settings keep the Ubuntu defaults unless selected.

| Check | Live result on `nuc` |
| --- | --- |
| Working VM `pm-noble` (126) | Finished unattended. A separate SSH client verified key-only login, `jq`, UFW, manual background updates and the Ansible-created directory. A later one-off script ran through Operations using the saved login. |
| Template `noble-base` (128) | Finished from the Ubuntu Server ISO with the virtual kernel. Installed `jq` and `ripgrep`, ran an Ansible library task, removed its installer media and shut down as a reusable Proxmox cloud-init template. |
| Linked clone `noble-link` (130) | Booted with its own hostname and separate SSH login and key. Verified inherited packages and directory, absence of the source account's authorized keys, and successful post-clone software. |

The final dashboard image was deployed with the queue idle. The real browser confirmed ISO selection, software review and discovery of the finished template. GitHub lookup was checked against the public API and frozen-key handling in the automated tests. 260 backend tests, 46 UI tests and network-isolated image checks passed. Earlier failed installer fixtures 116, 117, 120, 123 and 127 are stopped and retained for inspection.

## Connection shortcuts and typed inputs

The connection shortcuts and typed Ansible inputs are complete and deployed. The dashboard container was recreated with the job queue idle. The deployed application revision is [4a77803](https://github.com/rootisgod/proxmate/commit/4a77803a75f94c6b743da0c3b201878d94dcccb1).

| Workflow | Verified on the live dashboard |
| --- | --- |
| Open a Windows desktop | **Operations → NUC-PM-Win → Open desktop** downloaded an RDP file for the guest's current listener. The file prompts for credentials. |
| Connect to Linux | **Copy SSH command** on `NUC-Linux-Build` selected its LAN address from seven guest-agent addresses by matching the Proxmox network adapter. |
| Run an Ansible script | The script editor displayed dropdown, yes/no and free-drive-letter controls. Drive discovery on the Windows guest reported C: through F: in use. A Windows service action reached review without executing it. |

The release passed 257 backend tests, 46 UI tests and the network-isolated container checks. Automated tests cover completion notifications, reused VM IDs and a drive letter becoming occupied after review. Live browser verification covered the shortcuts on existing guests and the editor. The Windows ISO installation recorded below is a separate, earlier acceptance run.

## Windows installation checkpoint — 2026-11-17

Clean recreation verified: at the user's request the previous nuc/104 VM, its three attached disks and exact generated answer ISO were deleted after identity checks. Job a150235eeb2443bfb426b76cce0540c4 recreated win-server with the latest helper and frozen MSI checksum from the start. It succeeded without interactive login, remote repair or a helper change during installation. Attached setup media was verified against the deployed helper. Final checks through certificate-validated WinRM: address 192.168.1.92, 7-Zip 26.06 installed, WinRM and QEMU-GA running, bootstrap-ready=ready and retry task removed. Proxmate health check passed. Original Windows/VirtIO installation ISOs were retained.

Live guest-tool correction: authenticated WinRM worked before the agent was available. Read-only disk diagnostics over pinned SSH showed the VirtIO 0.1.302 MSI is unsigned and the serial driver was already healthy. The helper now accepts an unsigned MSI only when its SHA-256 exactly matches the selected ISO, rejects changed bytes/bad signatures, and skips an already-healthy serial driver. The approved new guest was repaired through certificate-validated WinRM, after obtaining its public certificate through the verified Proxmox disk channel; the normal worker then verified QGA/WinRM and installed Chocolatey/7-Zip successfully. The final helper/checksum pipeline is in the deployed image. Checks: 166 backend tests, 13 mocked PowerShell tests and network-disabled container checks passed. Diagnostic Python packages/screenshots are confined to ignored data directories, not production dependencies.

Windows remote management before login (deployed; approved rebuild succeeded): extracted the packaged PowerShell helper, enabled WinRM before guest-tool installation, deferred MSI during Windows Setup, and added SYSTEM startup/timed retries capped at 60 attempts. The persisted script/directory is administrator/SYSTEM protected, overlapping attempts are locked, certificates remain stable across retries, and no AutoLogon is added. Local diagnostic logs and a safe stage file distinguish remote access, agent installation and authenticated readiness. Clone cleanup resets retry/certificate state. Proxmate still requires trusted guest-agent discovery and authenticated WinRM before software. Checks: all 165 backend tests, 9 mocked PowerShell behavior tests, generated-script parsing and network-disabled container checks passed. The user approved deleting and rebuilding the current test deployment. Job 46b013c3de9a3296cefb9f1610c649af was cancelled; verified VM 104 and its three attached disks/generated answer ISO were removed. The latest helper was deployed with a matching source hash. Replacement job 35bda5822811472181ae849290bf76f2 reuses the original Windows settings and Chocolatey/7-Zip selections. Its payload and connected-server hashes were checked before release. Local SQLite/key snapshot: /data/backup-before-winrm-rebuild-20261117 (same volume). Replacement job 35bda5822811472181ae849290bf76f2 succeeded: Windows Server 2025 Standard Desktop Experience on nuc/104, address 192.168.1.26, WinRM and QEMU-GA running, Chocolatey and 7-Zip 26.06 installed, retry task removed. No interactive Windows login was used. During validation, the helper needed a remote repair described below; the subsequent clean recreation above now verifies a full install with that final repair already embedded.

Product-key prompt correction (deployed after user-requested cancellation): Windows answer XML previously omitted ProductKey, and VM 105 stopped at the key screen. Simple plans now derive Microsoft public GVLKs from exact WIM build and EDITIONID for non-evaluation Server 2019/2022/2025 Standard and Datacenter. The key is written only in windowsPE UserData, with errors still visible; activation remains a separate licensed step, explained in review and README. Unknown/evaluation images receive no guessed key, and private keys are not accepted by setup_key. The original VM 105 answer media was not modified in place. The user subsequently requested cancellation/deletion: its worker was stopped, job marked cancelled, recorded SMBIOS/vmgenid identity checked, VM stopped and deleted with only its three attached deployment disks, and its exact generated answer ISO deleted after checking other VM attachments. Windows/VirtIO ISOs and cancelled job history were retained. Proxmate was restarted using the latest image; no replacement build was queued. 68 focused provisioning/simple-flow tests passed, including ISO round trips and edition/key exclusions. The subsequent clean recreation above verified the new answer media end to end.

Windows first-boot correction (deployed during approved queue recovery): VM 104 reached the DVD prompt and timed out, with all three ISOs correctly attached. New unattended Windows builds now receive bounded space-key input during the first 20 seconds, guarded by their generated SMBIOS identity, answer-media attachment and running/uptime checks. Disk-first boot allows subsequent installation reboots to use the disk. Existing VM start actions never send keys. 56 focused provisioning/simple-flow tests passed, including input limits, changed targets/media, stale boot, API failure and manual/Linux/existing-VM exclusions. The subsequent clean recreation above verified automatic boot input on this host. Bootstrap now checks the original machine identity and inventory on each poll, fails promptly for removed/moved/replaced VMs, retries temporary node/API outages and logs waiting progress at least once per minute while polling. 90 focused lifecycle/provisioning/simple-flow tests passed. Initial deployment was deferred while the first build ran. Subsequent read-only checks found VM 104 had been removed externally; Proxmate was still waiting for bootstrap. With explicit user approval, that stale job was marked failed, the original queued win-server2 request was held across deployment, and its payload/server hashes were checked before release. No existing VM was stopped, reset or deleted. A local SQLite/key snapshot is at /data/backup-before-queue-recovery-20261117 (same volume). After release, the existing win-server2 request created and started VM 105 and sent 17 boot space keys within the bounded window. At that checkpoint it was waiting for authenticated guest readiness; the later user-requested replacement and clean recreation above supersede that run. The deployed image also passed network-disabled container checks.

VirtIO detection correction: nuc's virtio-win-0.1.302.iso contains both guest-agent/qemu-ga-x86_64.msi and vioserial/2k25/amd64/vioser.inf, but exposes their long filenames through Rock Ridge rather than Joliet/UDF. The preflight check now reads Rock Ridge names as well as the existing namespaces; no older driver fallback or signature bypass was introduced. Synthetic regression media reproduced the false rejection before the fix, and missing-driver/guest-agent cases still fail correctly. All 56 focused simple-flow/lifecycle tests passed. The rebuilt image successfully detected the actual remote ISO read-only; no guest installation or VM mutation was performed.

Simplified Windows Server / Ubuntu interface (0.6.0) is deployed locally. Home now offers Windows, Ubuntu, or software/scripts for existing VMs. A three-page wizard selects named ISO editions, machine quantity, packages/features and saved scripts. It discovers resource defaults, queues one job per guest, saves credentials/connections automatically, waits for authenticated remote access, applies frozen tasks and optionally generalizes a template. Ubuntu uses generated NoCloud media in ISO storage without requiring snippets support. Advanced tools are retained behind a collapsed navigation section.

Read-only validation against the connected node `nuc` found suitable storage, vmbr0, Windows Server media and VirtIO ISOs. Local Server 2022 and 2025 images returned the expected four named editions. The initial SSH fingerprint mismatch is resolved: the user supplied the console-verified ED25519 SHA256 fingerprint, which was saved through the Server API. A subsequent authenticated, pinned SSH read successfully inspected the Windows Server 2025 ISO on nuc and returned all four Standard/Datacenter Core/Desktop Experience editions. Trust verification was not bypassed. No VM operations, uploads or guest scripts were performed. Actual Ubuntu preparation and guest installation remain untested on live disposable targets.

Validation: 129 backend tests and 15 DOM workflows passed, including three Windows machines plus software and applying software to existing guests. Backend checks cover batch idempotency, secret omission, resource conflicts, media metadata, matching guest drivers, authenticated task ordering, replaced VM identities and failed generalization. Windows bootstrap and generalization scripts passed PowerShell parsing without execution; built-in Ansible modules passed syntax checks in a network-disabled Docker container. End-to-end installation/Sysprep/cloud-init and rendered browser/mobile validation remain outstanding. No VMs were created, started, stopped or otherwise modified during this iteration; no media was uploaded to Proxmox. Eight historical jobs were retained. Local pre-upgrade snapshot: /data/backup-before-simple-flow-20261117 (same volume, not off-host).

Security review: the main new risk is running follow-up software or generalization against the wrong machine. Plans freeze exact new IDs, task bodies and credentials; creation refuses existing IDs, bootstrap validates guest addresses/trust, and template preparation checks the recorded machine identity. Failed setup/software prevents conversion. Templates require clone-specific login/trust setup; arbitrary custom scripts remain trusted administrator code. ISO revalidation compares WIM edition metadata, not a full ISO content checksum. Windows driver discovery checks matching files. New build plans freeze the MSI SHA-256 from the selected trusted ISO; execution and the guest verify it. Unsigned MSI wrappers require that matching pin; invalid signatures remain rejected and Windows enforces driver signatures. Discovered WinRM access is scoped to the selected LAN subnet and displayed in the review.

## Previous server connection iteration

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

- Exercise Windows Operations recovery on a disposable guest with a stopped WinRM service and with the guest agent unavailable. The [Windows acceptance checklist](docs/windows-workflows.md#disposable-guest-acceptance) describes the remaining cases.
- On a fresh guided Ubuntu build, use a workstation public key and sign in with the SSH command from the completion notification. The existing-guest check above verified address selection and the command.
- Use approved disposable targets and archives for snapshot restore, backup retention, scheduled operations and CPU/RAM changes. Mobile and multi-node checks also remain open.
- The earlier nested Hyper-V test still has an unresolved DHCP issue. The November 20 guest checks used the homelab Proxmox node.

Security review: the main residual risk is trusted administrator code execution. Saved task YAML rejects delegation/connection overrides, guest runs suppress output, credential reads omit secrets, and reviewed plans expose guest endpoints. Guest-agent mismatches and endpoints resolving to the configured hypervisor/loopback are rejected. These controls do not make custom scripts a sandbox or protect a compromised running container.
