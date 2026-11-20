# Everyday Windows workflows

From **Create → Virtual machine → Windows Server**, a sysadmin should be able to choose a template or ISO, give the machine a Windows name, and create it. Leaving software and data disks empty should produce a plain, manageable Windows VM. A separate **Create → Reusable template → Windows Server** action should capture the setup that several machines will share.

The following examples define the workflow and its acceptance criteria. The delivery notes at the end distinguish automated coverage from checks that still need a disposable Windows guest.

## Deploy a VM from a template

Suppose I want a file server called `files-01`. I pick my Server 2025 template, enter the name once, add an 80 GiB data disk labelled `Data`, and select my saved “Configure file server” Ansible task with `share_name=Lab`. The review shows the new machine, disk and selected task. I can also create a plain server by providing just its name and template.

1. Allocate a free VM ID and clone the selected template. Default to a **full clone** so the VM owns its disks and can outlive its template. A **linked clone** saves space and creation time but depends on its base image. Offer it in Advanced where storage supports it. [Proxmox clone types](https://github.com/proxmox/pve-docs/blob/master/qm.adoc#qm_copy_and_clone)
2. Apply the chosen name inside Windows during first-boot setup. Use it for the Proxmox display name too. Give the clone its own management certificate and a known guest login.
3. Wait through first boot and any required reboot. Verify the clone’s identity, final Windows name, reported address and authenticated HTTPS WinRM connection. Show progress while other machines can run jobs.
4. Prepare the requested data disks, then run packages and saved tasks in the reviewed order. Finish on the machine page with the actual hostname, addresses, login check and per-task results.

**Acceptance example.** Two VMs built from the same template report their respective requested names and distinct guest certificates. Only `files-01` receives the data disk and share. A failed login leaves the clone accessible from **Open console** and explains which step is waiting.

## Build a VM from an ISO

The source picker should read the local ISO library and accessible Proxmox ISO storage. Recognize filename hints such as `win`, `windows`, `2019`, `2022` and `2025`. Show a short list such as **Windows Server 2022** and **Windows Server 2025**. Expand an item to see the actual filename and storage. Keep driver media and application installers out of the Windows list, and offer **Browse all media** for unusually named images.

Use the image metadata to verify the available editions. A filename containing `2025` is a discovery hint. Only the editions found inside that ISO belong in the edition picker, including the distinction between Server Core and Desktop Experience. Cache inspection results by media identity so opening the wizard stays fast.

I choose the edition, hostname and guest login. I can enable RDP, choose **Manual patching** for this lab build, install Chocolatey and named packages, and select saved Ansible tasks with parameters. Default to the Windows update policy supplied by the image unless I explicitly choose a different policy. Selecting a common program includes Chocolatey automatically. To enter other package IDs, I tick **Set up Chocolatey** and use **Browse Chocolatey packages** to find their IDs. With no packages or scripts selected, stop after verifying Windows is manageable.

Proxmate picks a compatible node, storage, network, firmware, TPM and driver media. Show the effective choices in an expandable review. Keep encryption policy under the operator’s control. Finish with the job result, verified WinRM status, console link and the answer file used for this build.

### Choose what a fresh machine gets

On **Software**, the following choices apply to Windows ISO installs and new clones. The review lists the selected tasks before any machine is created. These options draw on [PowerShell TUI MAX](https://github.com/iaingblack/powershell-tui-max/blob/d66e2c323a799b202a3fce8e5d0caf84b1c68e1b/TuiMax.ps1).

| In the wizard | What you can choose |
| --- | --- |
| Windows settings | Enable Remote Desktop with Network Level Authentication, select manual updates, or enable OpenSSH Server. The SSH firewall rule allows TCP 22 from the reviewed management IP or subnet. HTTPS WinRM is already part of provisioning. |
| Desktop preferences | Skip Edge first-run prompts, show hidden files and extensions in Explorer for the build account and future profiles, or keep Server Manager closed at sign-in. The ISO edition picker identifies Server Core and disables the desktop preferences for that edition. |
| Common programs | Choose Visual Studio Code, Git, 7-Zip, Notepad++, PowerShell 7, Windows Terminal, Python 3 or Node.js LTS. Each package is scheduled once, even if you also type its ID. Terminal requires Desktop Experience and build 19041 or later. |
| Windows Server features | Add the Telnet **client** or .NET Framework 3.5 alongside roles such as IIS. For .NET 3.5 on ISO builds, Proxmate looks for the installer's feature files on the mounted DVD. Clones use their configured Windows feature source. |
| Security and console sign-in | Disable legacy IE Enhanced Security where installed, turn off all Windows Firewall profiles, or automatically sign in once on the next boot of a working VM. Each requires confirmation on review. Turning off the firewall also removes its inbound management restrictions. Defender real-time protection follows the guest's own policy. |

Automatic sign-in runs after the selected software and any feature reboots. Ansible stores the build login password in a Windows LSA secret, which local administrators can access. Reusable templates omit the sign-in option. Chocolatey packages and Windows capabilities need access to their configured sources.

## Build a reusable Windows template

I choose the ISO and edition, common Windows settings, packages and scripts. For example, a Server 2025 Desktop Experience template might include 7-Zip and my baseline settings. Host-specific tasks such as creating a share belong on the clone.

Install and verify the baseline first. Run Sysprep generalization on the build VM, shut it down, then publish the stopped template. On the next power-on of **each clone**, Windows runs its specialize pass and first-boot setup. This is when the clone should receive its own name and management identity. If Sysprep fails, keep the build VM and surface the error before publishing a template. [Microsoft Windows configuration passes](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/how-configuration-passes-work?view=windows-11)

## Shared controls

| What I want to do | Expected behavior |
| --- | --- |
| Add data disks | Add one or more sizes, optional labels and optional drive letters. Default to NTFS with an automatically assigned drive letter. ISO builds reserve D:, E: and F: for setup DVDs. The picker also disables letters selected for another data disk. Record each newly allocated disk’s identity and match it inside this VM before formatting. If the match is ambiguous, the disk contains data, or the letter is occupied, stop and identify the disk that needs attention. A repeat run recognizes a volume already prepared by this build. |
| Reuse a Windows answer file | As soon as XML is generated, attach the exact version to the build record, including failed builds. Keep the original encrypted with administrator-only download. Make a reusable copy with credential and product-key placeholders the default download. Identify the source ISO, edition and any companion bootstrap script needed to use it elsewhere. |
| Manage my Ansible library | Browse, read and edit scripts from the main navigation. Define named inputs with defaults and show their values during build review. Record the reviewed script revision with each run. GitHub-backed import or sync can follow later. |
| See which Windows machines I can manage | Show the last successful authenticated WinRM check and its time in the inventory. Distinguish unchecked guests, stopped guests, unavailable agents, unreachable listeners, certificate errors and rejected logins. An unreachable port alone cannot establish that WinRM is disabled. |
| Enable WinRM | Offer **Enable HTTPS WinRM** when an authenticated guest-agent channel can configure that particular VM. Show the intended firewall scope and apply the change on request. Without that channel, show copyable PowerShell commands for an elevated console on the guest. Offer **Test again** and mark the machine manageable after a certificate-validated login succeeds. |
| Open the console | Keep **Open console** on the machine and on a build that needs attention. Open that VM’s Proxmox console directly in a separate window or tab. A Proxmox sign-in may be needed. |

## Delivery and verification

The guided Windows wizard implements both source paths. **Full copy** is the default. Linked copies use the template’s base storage and depend on Proxmox support for that image and storage. A template must be stopped, generalized and have the guest agent installed. The source stays on its node. The review records its configuration identity. A change to the source requires a new review.

Each clone gets setup media for the specialize and OOBE passes, a fresh bootstrap identity and the requested computer name. Proxmate waits for the new guest to report that name and its per-clone marker, verifies its HTTPS login, then prepares up to three newly allocated data disks and applies software. The job log links to the console and the archived answer files. A failed or interrupted mutation leaves the VM available for inspection.

ISO names are grouped for discovery. The edition list comes from the selected image. Each generated answer is saved encrypted with the job. The reusable ZIP contains placeholders for the password and product key. An explicit original download includes credentials. Archives begin with builds performed on this version.

The script library supports named inputs and defaults on saved Ansible task lists. Scripts and input values are frozen during build review. The machine page supports targeted WinRM inspection, a reviewed firewall scope and console commands. A successful login supplies the verification time shown in inventory. GitHub synchronization is deferred.

If a script fails because the saved address differs from the guest agent’s addresses, open the failed job and choose **Refresh connection** for the affected VM. The same button is on every VM row under **Operations** and in the machine’s **Network** panel. Check the current address and saved login, then choose **Test and save**. After verification, start a new script run for the affected VM. If the VM was rebuilt and its certificate also changed, verify the new public certificate in the VM console before updating **Guest connection → Manual settings**.

### Run the Windows ISO acceptance test

From PowerShell in the project directory, supply the job ID of an earlier Windows Server Desktop Experience ISO build on this dashboard. The test creates one new VM with that build's edition, placement, CPU, memory and system disk. The source build must have one system disk and no extra data disks. Its ISO and driver media must still be available.

~~~powershell
$sourceJob = Read-Host 'Source Windows build job ID'
Get-Content -Raw tests/live_windows_build.py | docker compose exec -T proxmate python -u - --source-job $sourceJob
~~~

The runner prints its random seed, machine name, VM ID and job ID. It picks two packages from Git, Notepad++ and 7-Zip, and two desktop settings from Edge first-run prompts, Server Manager at sign-in and Explorer preferences. The VM gets a fresh Administrator password. The dashboard keeps the login and build history.

A pass requires a successful dashboard job and independent checks over certificate-verified WinRM. The checks cover the guest hostname, edition, system disk, installed package records and executables, and the selected registry values. The runner also compares CPU and memory to the review. The test leaves the guest running for inspection.

To repeat a selection on a new VM, add `--seed <printed-seed>` to the command above. To reconnect to the **same** job after closing the terminal, use its test job ID.

~~~powershell
$testJob = Read-Host 'Live test job ID'
Get-Content -Raw tests/live_windows_build.py | docker compose exec -T proxmate python -u - --verify-job $testJob
~~~

### Disposable-guest acceptance

1. Create a Server 2022 or 2025 template from a known ISO with one small Chocolatey package. Verify the edition, package, successful Sysprep and powered-off template.
2. Create two differently named clones. Give one a new data disk and a parameterized task. Compare their Windows names and certificate thumbprints. Check the new volume, task result and answer-file downloads. Confirm that the base template and the second guest kept their original disks.
3. Test WinRM inspection with an existing listener, a stopped service and an unavailable guest agent. Use the offered setup on a disposable guest and verify a login from the reviewed management address.

Automated tests cover generated media, mocked Proxmox and guest responses, queue resumption, script compilation, browser interactions and disk preparation with mocked Windows storage cmdlets. The checklist above requires real Windows and Proxmox.
