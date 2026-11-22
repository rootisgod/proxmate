# Portable lab packs

A lab pack describes how to create a lab on a fresh Proxmate installation. Keep its files in Git and share a ZIP with `lab.yaml` at the root. From the folder, use `zip -r ../lab.zip lab.yaml tasks/` or PowerShell's `Compress-Archive -Path lab.yaml,tasks -DestinationPath ../lab.zip`. The receiver needs Proxmate, compatible installation media or prepared templates, and guest access to any repositories the installer or tasks use. The dashboard includes a downloadable Ubuntu cloud example with one package and one bundled Ansible task.

## File format

Version 1 defines new guests. Version 2 adds task lists for existing VMs and can mix these with newly provisioned machines. The dashboard writes version 1 for a create-only lab and version 2 when any role uses an existing VM. A dashboard that supports only version 1 rejects a version 2 pack.

```yaml
version: 1
name: Development lab
machines:
  dc01:
    os: windows
    source:
      kind: iso
      hint: Windows-Server-2025.iso
      edition: Windows Server 2025 Standard (Desktop Experience)
    settings:
      cores: 2
      memory: 4096
      disk: 64
    tasks:
      - run: windows-domain-controller
        version: 1
        with:
          domain_name: dev.example.test
          dsrm_password: {prompt: restore_password}
  web01:
    os: ubuntu
    source: {kind: iso, hint: ubuntu-24.04-live-server-amd64.iso}
    after: [dc01]
    tasks:
      - run: linux-package
        version: 1
        with: {package: curl}
      - file: tasks/tools.yml
        with: {name: LabTools}
scripts:
  tasks/tools.yml:
    name: Create tools directory
    platform: linux
    kind: ansible
    parameters: {name: Directory name}
    defaults: {name: LabTools}
```

`tasks/tools.yml` is a guest task list, for example

```yaml
- name: Create the tools directory
  ansible.builtin.file:
    path: "/opt/{{ proxmate_inputs.name }}"
    state: directory
    mode: '0755'
```

### Machine and task contracts

- Each key under `machines` identifies one role. `after` names prerequisites in the same pack. Each prerequisite must finish successfully, including its guest tasks. Among roles whose prerequisites are satisfied, declaration order decides which is reviewed next. Portable backup uses that effective order when comparing same-named labs. Domain membership, DNS changes and other guest configuration require explicit tasks.
- Supported operating systems are `windows`, `ubuntu`, `debian` and `rocky`. Choose the guided installer's supported `iso`, `template` or Ubuntu `cloud` source. The hint is a filename or display name. A named Windows ISO edition must match the destination's inspected image. The pack holds neither installation images nor template disks.
- Portable settings follow the guided planner. Memory is in MiB and disk sizes are in GiB. Settings include hardware, VM or template purpose, Windows setup choices and data disks, Linux profile and username, Ubuntu setup options, and optional Linux password login. Omitted settings use guided defaults. Template sources inherit from the selected template. Credentials, machine IDs, media volume IDs, addresses, keys, storage and bridge names belong to the destination.
- Tasks run in list order. `run` selects a catalog contract by ID and version. The receiving Proxmate supplies that built-in implementation. `file` selects source bundled in the ZIP. Export captures the script body, inputs and defaults from the chosen build review, including library examples. Later library edits cannot change that copy.
- Store scripts directly under `tasks/` as Ansible task lists (`.yml`, `.yaml`), guest PowerShell (`.ps1`) or guest shell (`.sh`). Use Proxmate's supported guest modules. Portable Ansible interpolation accepts simple `{{ proxmate_inputs.name }}` argument references. Controller lookups, roles, includes and controller-local file sources fall outside v1. Inspect embedded code before approving it. It runs with the guest management login and includes anything its author hardcoded.
- A password-typed built-in input uses `{prompt: input_name}`. The receiving operator supplies a fresh value for that machine during review. The machine login is collected separately. Use distinct prompt names when separate secrets are needed.
- V1 admits up to 20 machines, 50 task selections per machine and 32 script files. ZIP and expanded payloads are bounded at 2 MiB, the manifest at 128 KiB, and individual script bodies at 100,000 characters. Repeated selections count towards an 8 MiB aggregate script budget. Machine keys use up to 15 letters, digits or hyphens and start with a letter. Destination names follow the guided planner's 12-character limit. Unknown versions, unexpected files, unsafe paths and dependency cycles fail validation.

## Review and execution

1. On **Labs → Create lab**, define new machines or use an existing VM as a starting point. Assign each a key, an OS, an install source and its ordered library tasks. Set prerequisites using the other machine keys. Saving captures the chosen custom scripts and their inputs. Password-typed inputs become deployment prompts.
2. **Save lab** stores the definition on this dashboard. A ZIP is only needed to transfer the definition, so you can go straight to setup or return later. Other ways to start include importing a hand-written ZIP or exporting one or more reviewed builds. Saved and imported packs share the same portable format and deployment workflow.
3. Map the machines to the selected Proxmox server. Discover available node, storage, bridge and source options. Inspect Windows editions. Supply credentials, any required prompts and a fresh destination name where needed.
4. Review the entire lab. Proxmate uses the guided planner for each machine and freezes the resulting plans. Confirm that review once to create a durable lab run. Repeating the confirmation returns the same run. A new deployment requires a new review.
5. Follow the run and its individual build jobs in the dashboard. A failed or interrupted prerequisite blocks unstarted dependents. Inspect partial machines before deciding how to recover. V1 creates new guests and records the jobs that belong to each deployment. Updating, adopting or deleting existing machines is outside this format.

After a service restart, confirmed lab machines that have never started remain eligible to run when their prerequisites finish. A recorded guest-readiness wait can resume its checks. A machine interrupted during a provisioning mutation needs inspection and its dependent machines stay blocked.

**Existing VMs as blueprints.** Proxmate reads the selected guest's QEMU configuration. A matching stable identity and guided build record let it recover the recorded installation choices and setup tasks. You can keep or remove those tasks, edit public inputs and add scripts from the current library. A VM with only hardware metadata provides its CPU count, memory and an identifiable system disk size. You choose its OS and install source when they cannot be recovered. Disk contents, later in-guest changes and source credentials are outside the portable definition.

For a role that creates a machine, deployment can supply `secrets.<role>.build_credential_id` from the destination dashboard's build defaults. A saved Windows login satisfies the Administrator password prompt. A Linux default supplies the automation key and must match an explicitly required login name. Optional or required workstation keys and Linux sign-in passwords are separate. Credential IDs and key material stay outside exported manifests.

## Running tasks on existing VMs in version 2

In **Create lab**, choose **Run scripts on an existing VM** and select the specific tasks that should run. A portable existing role uses `os: linux` or `os: windows`, `source.kind: existing`, optional `source.hint` for the VM display name, and one or more tasks. Hardware, installer media, VM IDs and credentials are excluded from that role. Here is a complete example with an Ubuntu build followed by a task on an existing Linux guest.

```yaml
version: 2
name: Tools rollout
machines:
  tools01:
    os: ubuntu
    source: {kind: cloud}
  tools-admin:
    os: linux
    source: {kind: existing}
    after: [tools01]
    tasks:
      - run: linux-package
        version: 1
        with: {package: curl}
```

The destination operator selects the running guest by node and VM ID. Review checks its identity and presents its login, target and tasks. A saved connection supplies the login. A missing connection requires a compatible credential. In-place work has its own confirmation at deployment. The task job follows the Operations execution path and rechecks the reviewed target before executing. A deployment records this machine as an existing target. A different environment must bind the role to its own VM.

Saved builds made before portable snapshots were introduced can be exported from their original guided job if it retained the settings and scripts. Alternatively, open the saved build, review its current tasks, and export that review. To repeat a whole lab, open its saved import and review it with fresh destination names. Downloading a saved import returns the original ZIP, including comments in hand-written YAML.

## Implementation and acceptance plan

1. Define a strict versioned manifest, in-memory archive reader/writer and task resolver. Test round trips, unsupported versions, unsafe paths, size limits, secret handling and dependency cycles.
2. Add export from frozen guided sources and import inspection. Test that the receiving library can be empty and that changes to the original script cannot affect the archive.
3. Reuse the guided review and worker. Reserve distinct destinations, enqueue the reviewed jobs together and gate claims on prerequisite outcomes. Verify retry, failure, restart and host isolation.
4. Expose compose, inspect, map, review and run history in Create. Publish the HTTP contract and agent examples. Exercise the dashboard and API across the import/review boundary before deployment.

A lab can start with a saved guided build. **Edit a copy** reopens a stored pack with its embedded step definitions, ordered inputs and named prompts. Saving a changed definition gives it a new pack ID. The original ZIP remains downloadable.
