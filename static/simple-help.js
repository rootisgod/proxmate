const guidedFieldHelp = {
  build_credential_id: 'Uses the saved login selected in Credentials. Choose a separate login to give this build its own Windows password or Linux SSH key.',
  purpose: 'A working VM is ready to use after setup. A reusable template shuts down after setup so you can create new VMs from it.',
  name: 'Sets the VM name in Proxmox and the hostname inside the guest. Use up to 12 letters, numbers or hyphens.',
  count: 'Creates this many VMs with the same settings. Proxmate adds numbered suffixes such as -01 and -02.',
  password: 'Sets the Windows Administrator password. Proxmate uses it to verify the guest and run your selected software.',
  username: 'The Linux account you sign in to over SSH. Proxmate also uses this account with its own automation key.',
  github_username: 'Imports public SSH keys from this GitHub account. Check the fingerprints in the build review to confirm who can sign in.',
  ssh_public_key: 'Paste an OpenSSH public key, such as the contents of id_ed25519.pub, to sign in from your own computer.',
  ubuntu_password_enabled: 'Lets this Linux account sign in with a password from the Proxmox console or over SSH. Proxmate keeps its own SSH key for automation.',
  ubuntu_password: 'Use 12–128 characters. You can enter this password at the Proxmox console or when SSH prompts you to sign in.',
  source_kind: 'An ISO runs the OS installer. A cloud image starts from a prepared Ubuntu system. A template copies a machine you have already prepared.',
  template_id: 'Choose the prepared VM that supplies the operating system, disk and network for this copy.',
  clone_mode: 'A full copy has independent disks. A linked copy uses less space and depends on the source template and its storage.',
  profile: 'Sets which official Ubuntu cloud image Proxmate prepares.',
  source: 'The operating system installer. Proxmox can use a copy already on the selected node or receive one from the local ISO library.',
  edition: 'Pick the edition found inside the ISO. Desktop Experience includes the full graphical desktop. Server Core uses a smaller install.',
  generalized: 'Sysprep lets Windows give each clone its own identity. The QEMU Guest Agent lets Proxmate verify the clone and configure it.',
  all_media: 'Show every ISO visible to this build. Use this to find a Windows installer with an unusual filename.',
  node: 'The Proxmox host where the VM runs. This determines which disks, ISOs and network bridges are available.',
  storage: 'Where Proxmox stores the VM disks. For a linked Windows copy, this choice applies to any new data disks.',
  bridge: 'Connects the VM’s network adapter to this Proxmox network. Use the bridge for the LAN or VLAN where the guest should be reachable.',
  cores: 'The number of virtual CPU cores available to each VM.',
  memory_gb: 'How much RAM Proxmox assigns to each VM, measured in GiB.',
  disk: 'The size of the new disk that holds the operating system. Copies inherit the template’s system disk size.',
  agent_iso: 'The VirtIO ISO supplies Windows drivers and the QEMU Guest Agent, which reports the guest IP and helps Proxmate verify the login.',
  management_cidr: 'The IP address or subnet allowed to connect to Windows for automation. For one IPv4 address use /32, for example 192.168.1.10/32.',
  locale: 'Sets the regional formats and keyboard layout used during Windows setup.',
  timezone: 'Sets the guest’s time zone, which affects the local time it displays.',
  disk_size: 'The capacity of this extra data disk. Proxmate verifies the new disk and formats it as NTFS.',
  disk_label: 'The name Windows shows for this volume in File Explorer.',
  disk_letter: 'Choose a free drive letter or let Windows assign one. Windows ISO builds reserve D:, E: and F: for the setup DVDs.',
  ubuntu_firewall: 'Keep Ubuntu’s default, enable UFW with SSH allowed on TCP 22, or disable UFW for this VM.',
  ubuntu_updates: 'Controls automatic updates after the VM is installed. The ISO installer can still apply its own security updates during setup.',
  ubuntu_install_source: 'Standard includes tools for interactive administration. Minimized starts with fewer packages. Both can use SSH, packages and Operations.',
  rdp: 'Allows Remote Desktop sign-in with Network Level Authentication. Connect using Open desktop on the finished VM.',
  manual_updates: 'Disables automatic Windows updates for this build so you can apply them on your own schedule.',
  chocolatey: 'Installs the Chocolatey package manager. Tick this to enter your own package IDs.',
  packages: 'Enter package IDs from Chocolatey on Windows, DNF on Rocky Linux, or APT on Debian and Ubuntu.',
  confirmed: 'Approves the listed VMs, resource choices and software. Proxmox creates and starts the new guests when you submit.',
  risk_confirmed: 'Approves the sensitive settings or trusted custom scripts listed in the review.',
};

const setupMediaHelp = {
  windows: 'Stores the Windows answer ISO that Proxmate generates.',
  iso: 'Stores a temporary ISO that Proxmate generates with the Linux installer answers and SSH key settings. Proxmate removes it after it verifies SSH.',
  cloud: 'Stores the generated cloud-init ISO that sets up the Ubuntu login, SSH keys and guest tools on the first boot.',
  template: 'A Linux clone gets its cloud-init drive on the VM disk storage. This choice applies to ISO and cloud image installs.',
};

function wizardHelpText(control, draft) {
  const {name, value} = control;
  if (name === 'iso_storage') {
    const kind = draft.os === 'windows' ? 'windows' : draft.source_kind;
    return setupMediaHelp[kind] + ((kind === 'windows' && draft.source_kind !== 'template') || kind === 'iso'
      ? ' An installer already on Proxmox stays where it is. An installer from the local ISO library is copied here.' : '');
  }
  if (name === 'source_kind' && draft.os === 'rocky') return 'Install Rocky Linux 10 from an ISO or copy a prepared Rocky template.';
  if (name === 'source' && draft.os === 'rocky') return 'DVD includes the installation packages. Minimal uses local packages and downloads extra guest tools. Boot downloads the operating system packages. Minimal and Boot need access to Rocky’s HTTPS mirrors.';
  if (name === 'source_kind' && draft.os === 'debian') return 'Install Debian from the selected DVD or create a copy of a prepared Debian template.';
  if (name === 'source_kind' && draft.os === 'windows')
    return 'An ISO installs Windows from scratch. A template copies Windows and any software already on the prepared machine.';
  if (name === 'windows_setting') {
    const details = {
      'edge-first-run': 'Skips the first-launch setup screens when a user opens Microsoft Edge on this VM.',
      'server-manager': 'Stops Server Manager from opening automatically when a user signs in.',
    };
    return simpleOptions.windows_settings?.find(item => item.id === value)?.detail || details[value];
  }
  if (name === 'feature') {
    const details = {
      'Web-Server': 'Adds IIS so Windows can host websites and web applications.',
      'FS-FileServer': 'Enables the Windows Server role for sharing files with other machines.',
      'Windows-Server-Backup': 'Installs the Windows tools for backup and recovery.',
      'RSAT': 'Installs tools for administering Windows Server roles and services.',
      'NET-Framework-Core': 'Adds .NET Framework 3.5 for applications that require it.',
      'NET-Framework-45-Features': 'Adds .NET Framework 4.x components for applications that require them.',
      'Telnet-Client': 'Adds the Telnet client for connecting to other hosts. Telnet connections send traffic in clear text.',
    };
    return details[value];
  }
  if (name === 'common_program') return `Installs ${value} using ${draft.os === 'windows' ? 'Chocolatey' : draft.os === 'rocky' ? 'DNF' : 'APT'} after the guest is ready.`;
  if (name === 'saved_script') return mg?.tasks.find(task => task.id === value)?.description || 'Runs this library script after the guest login is verified.';
  if (control.hasAttribute('data-script-param')) return 'This value is passed to the selected Ansible script. The review shows what will be used.';
  return guidedFieldHelp[name.replace(/_\d+$/, '')];
}

const guidedHelp = (() => {
  const dialog = document.querySelector('#simple-dialog');
  const fields = document.querySelector('#simple-fields');
  const bubble = document.createElement('div');
  bubble.id = 'guided-help-popup';
  bubble.className = 'guided-help-popup';
  bubble.hidden = true;
  bubble.setAttribute('aria-hidden', 'true');
  dialog.append(bubble);

  let active, pinned, timer;
  function close() {
    clearTimeout(timer);
    active?.setAttribute('aria-expanded', 'false');
    bubble.hidden = true;
    active = null;
    pinned = false;
  }
  function show(button) {
    clearTimeout(timer);
    if (active === button) return;
    close();
    active = button;
    active.setAttribute('aria-expanded', 'true');
    bubble.textContent = document.getElementById(button.getAttribute('aria-describedby')).textContent;
    bubble.hidden = false;
    place();
  }
  function place() {
    if (!active) return;
    const bounds = dialog.getBoundingClientRect();
    const anchor = active.getBoundingClientRect();
    const viewport = fields.getBoundingClientRect();
    if (anchor.bottom < viewport.top || anchor.top > viewport.bottom) {
      close();
      return;
    }
    const box = bubble.getBoundingClientRect();
    const offset = 8;
    const left = Math.max(offset, Math.min(anchor.left - bounds.left, bounds.width - box.width - offset));
    const below = anchor.bottom - bounds.top + 4;
    const above = anchor.top - bounds.top - box.height - 4;
    bubble.style.left = left + 'px';
    bubble.style.top = (below + box.height <= bounds.height - offset ? below : Math.max(offset, above)) + 'px';
  }
  const helpButton = element => element?.closest?.('.guided-help-button');
  const scheduleClose = () => {
    if (!pinned && document.activeElement !== active) timer = setTimeout(close, 150);
  };
  fields.addEventListener('pointerover', event => {
    const button = helpButton(event.target);
    if (button && event.pointerType === 'mouse' && !pinned) show(button);
  });
  fields.addEventListener('pointerout', event => {
    if (active && event.pointerType === 'mouse' && helpButton(event.target) === active &&
        helpButton(event.relatedTarget) !== active) scheduleClose();
  });
  fields.addEventListener('focusin', event => {
    const button = helpButton(event.target);
    if (button) show(button);
    else close();
  });
  fields.addEventListener('focusout', event => {
    if (active && event.target === active) close();
  });
  fields.addEventListener('click', event => {
    const button = helpButton(event.target);
    if (!button) return;
    if (active === button && pinned) close();
    else {
      show(button);
      pinned = true;
    }
  });
  bubble.addEventListener('pointerenter', () => clearTimeout(timer));
  bubble.addEventListener('pointerleave', scheduleClose);
  fields.addEventListener('scroll', place, {passive:true});
  dialog.addEventListener('pointerdown', event => {
    if (!helpButton(event.target) && !bubble.contains(event.target)) close();
  });
  dialog.addEventListener('keydown', event => {
    if (active && event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  }, true);
  dialog.addEventListener('close', close);
  window.addEventListener('resize', close);

  function attach(draft) {
    fields.querySelectorAll('input,select,textarea').forEach((control, index) => {
      const label = control.closest('label');
      const text = wizardHelpText(control, draft);
      if (!label || !text) return;
      const caption = [...label.childNodes].filter(node => node !== control).map(node => node.textContent).join('').trim();
      const checkbox = control.type === 'checkbox';
      const wrapper = document.createElement('div');
      wrapper.className = checkbox ? 'guided-check' : 'guided-field';
      if (label.classList.contains('wide')) wrapper.classList.add('wide');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'guided-help-button';
      button.setAttribute('aria-label', 'Help for ' + caption);
      button.setAttribute('aria-expanded', 'false');
      button.setAttribute('aria-controls', bubble.id);
      const glyph = document.createElement('span');
      glyph.setAttribute('aria-hidden', 'true');
      glyph.textContent = 'i';
      button.append(glyph);
      const description = document.createElement('span');
      description.id = 'guided-help-' + index;
      description.hidden = true;
      description.textContent = text;
      button.setAttribute('aria-describedby', description.id);
      control.setAttribute('aria-describedby', [control.getAttribute('aria-describedby'), description.id].filter(Boolean).join(' '));
      label.before(wrapper);
      if (checkbox) wrapper.append(label, button);
      else {
        const title = document.createElement('div');
        title.className = 'guided-field-caption';
        if (!control.id) control.id = 'guided-field-' + index;
        label.htmlFor = control.id;
        title.append(label, button);
        wrapper.append(title, control);
      }
      wrapper.append(description);
    });
  }
  return {attach, close};
})();
