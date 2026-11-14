"""Pure provisioning plans, validated profiles and Windows answer media."""
import io
import ipaddress
import re
import xml.etree.ElementTree as ET

PROFILES = {
    'ubuntu-22.04': dict(label='Ubuntu 22.04 LTS', user='ubuntu', base='https://cloud-images.ubuntu.com/jammy/current/', image='jammy-server-cloudimg-amd64.img', checksums='SHA256SUMS'),
    'ubuntu-24.04': dict(label='Ubuntu 24.04 LTS', user='ubuntu', base='https://cloud-images.ubuntu.com/noble/current/', image='noble-server-cloudimg-amd64.img', checksums='SHA256SUMS'),
    'ubuntu-26.04': dict(label='Ubuntu 26.04 LTS', user='ubuntu', base='https://cloud-images.ubuntu.com/resolute/current/', image='resolute-server-cloudimg-amd64.img', checksums='SHA256SUMS'),
    'fedora-44': dict(label='Fedora Cloud 44', user='fedora', base='https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/x86_64/images/', image='Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2', checksums='Fedora-Cloud-44-1.7-x86_64-CHECKSUM'),
    'rocky-9': dict(label='Rocky Linux 9.8', user='rocky', base='https://download.rockylinux.org/pub/rocky/9/images/x86_64/', image='Rocky-9-GenericCloud-Base-9.8-20260525.0.x86_64.qcow2', checksums='CHECKSUM'),
}
LOCALES = {'en-US': 'English (United States)', 'en-GB': 'English (United Kingdom)', 'de-DE': 'German', 'fr-FR': 'French', 'es-ES': 'Spanish', 'ja-JP': 'Japanese'}
TIMEZONES = ['GMT Standard Time', 'UTC', 'W. Europe Standard Time', 'Eastern Standard Time', 'Pacific Standard Time', 'Tokyo Standard Time', 'AUS Eastern Standard Time']


def linux_options(data):
    profile = data.get('profile', 'ubuntu-24.04')
    if profile not in PROFILES: raise ValueError('Choose a supported Linux image profile')
    if profile == 'rocky-9' and int(data.get('disk', 32)) < 10:
        raise ValueError('Rocky Linux requires at least a 10 GB disk')
    if data.get('outcome', 'template') not in ('template', 'vm'): raise ValueError('Outcome must be vm or template')
    if data.get('outcome', 'template') == 'template' and data.get('start'):
        raise ValueError('A template cannot be started; choose a VM instead')
    mode = data.get('network_mode', 'dhcp')
    if mode not in ('dhcp', 'static'): raise ValueError('Network mode must be dhcp or static')
    if mode == 'static':
        try:
            address = ipaddress.IPv4Interface(data.get('address', ''))
            gateway = ipaddress.IPv4Address(data.get('gateway', ''))
            if gateway not in address.network: raise ValueError()
        except ValueError as exc: raise ValueError('Provide IPv4/CIDR and a gateway in the same subnet') from exc
    if data.get('dns'):
        try:
            for value in data['dns'].split(): ipaddress.ip_address(value)
        except (ValueError, AttributeError) as exc: raise ValueError('DNS must contain IP addresses separated by spaces') from exc


def windows_options(data):
    if not isinstance(data, dict): raise ValueError('Windows settings must be an object')
    defaults = dict(ui_language='en-US', locale='en-GB', keyboard='en-GB', timezone='GMT Standard Time', hostname='PROXMATE', image_index=1, rdp=False, accept_eula=False, wipe_disk=False)
    if set(data) - set(defaults) - {'admin_password'}: raise ValueError('Unknown Windows setting')
    d = dict(defaults, **data)
    for field in ('ui_language', 'locale', 'keyboard'):
        if d[field] not in LOCALES: raise ValueError(f'Unsupported Windows {field}')
    if d['timezone'] not in TIMEZONES: raise ValueError('Unsupported Windows timezone')
    if not isinstance(d['hostname'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,14}', d['hostname']) or d['hostname'].isdigit() or d['hostname'].endswith('-'):
        raise ValueError('Windows hostname must be 1–15 letters/digits/hyphens, not all numeric')
    if isinstance(d['image_index'], bool) or not str(d['image_index']).isdigit() or not 1 <= int(d['image_index']) <= 100:
        raise ValueError('Image index must be 1–100; confirm it against your ISO with DISM')
    d['image_index'] = int(d['image_index'])
    for field in ('rdp', 'accept_eula', 'wipe_disk'):
        if not isinstance(d[field], bool): raise ValueError(f'{field} must be a boolean')
    password = d.get('admin_password', '')
    if not isinstance(password, str): raise ValueError('Administrator password must be text')
    if password and (not 12 <= len(password) <= 128 or not all((re.search('[A-Z]', password), re.search('[a-z]', password), re.search('[0-9]', password), re.search('[^A-Za-z0-9]', password))) or any(not (32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in password)):
        raise ValueError('Administrator password must be 12–128 characters with upper/lower case, a number and a symbol; no control characters')
    return d


def answer_xml(settings, preview=False, bootstrap=None):
    d = windows_options(settings)
    if not preview and (not d['accept_eula'] or not d['wipe_disk']):
        raise ValueError('Confirm Windows licensing and formatting the new VM disk')
    ns, wcm = 'urn:schemas-microsoft-com:unattend', 'http://schemas.microsoft.com/WMIConfig/2002/State'
    ET.register_namespace('', ns); ET.register_namespace('wcm', wcm)
    def add(parent, tag, text=None, **attrs):
        child = ET.SubElement(parent, '{'+ns+'}'+tag, attrs)
        if text is not None: child.text = str(text).lower() if isinstance(text, bool) else str(text)
        return child
    def component(parent, name):
        return add(parent, 'component', name=name, processorArchitecture='amd64', publicKeyToken='31bf3856ad364e35', language='neutral', versionScope='nonSxS')
    root = ET.Element('{'+ns+'}unattend')
    pe = add(root, 'settings', **{'pass': 'windowsPE'})
    intl = component(pe, 'Microsoft-Windows-International-Core-WinPE')
    add(add(intl, 'SetupUILanguage'), 'UILanguage', d['ui_language'])
    for tag, field in [('InputLocale','keyboard'),('SystemLocale','locale'),('UILanguage','ui_language'),('UserLocale','locale')]: add(intl, tag, d[field])
    setup = component(pe, 'Microsoft-Windows-Setup')
    disks = add(setup, 'DiskConfiguration'); disk = add(disks, 'Disk', **{'{'+wcm+'}action':'add'})
    add(disk, 'DiskID', 0); add(disk, 'WillWipeDisk', True)
    partitions = add(disk, 'CreatePartitions')
    # GPT: recovery, EFI, MSR, Windows. Only the new VM's disk 0 is targeted.
    for order, kind, size in [(1,'Primary',1024),(2,'EFI',260),(3,'MSR',16),(4,'Primary',None)]:
        part = add(partitions, 'CreatePartition', **{'{'+wcm+'}action':'add'})
        add(part,'Order',order); add(part,'Type',kind); add(part,'Extend' if size is None else 'Size',True if size is None else size)
    changes = add(disk, 'ModifyPartitions')
    for order, part_id, fmt, label in [(1,1,'NTFS','Recovery'),(2,2,'FAT32','System'),(3,4,'NTFS','Windows')]:
        part = add(changes,'ModifyPartition', **{'{'+wcm+'}action':'add'})
        for tag,value in [('Order',order),('PartitionID',part_id),('Format',fmt),('Label',label)]: add(part,tag,value)
        if part_id == 1: add(part,'TypeID','de94bba4-06d1-4d40-a16a-bfd50179d6ac')
        if part_id == 4: add(part,'Letter','C')
    add(disks,'WillShowUI','OnError')
    osimage = add(add(setup,'ImageInstall'),'OSImage')
    meta = add(add(osimage,'InstallFrom'),'MetaData', **{'{'+wcm+'}action':'add'})
    add(meta,'Key','/IMAGE/INDEX'); add(meta,'Value',d['image_index'])
    target = add(osimage,'InstallTo'); add(target,'DiskID',0); add(target,'PartitionID',4)
    add(osimage,'WillShowUI','OnError'); add(add(setup,'UserData'),'AcceptEula',d['accept_eula'])
    specialize = add(root,'settings', **{'pass':'specialize'})
    if bootstrap and bootstrap.get('enabled'):
        from bootstrap import WINDOWS_COMMAND
        deployment = component(specialize,'Microsoft-Windows-Deployment')
        command = add(add(deployment,'RunSynchronous'),'RunSynchronousCommand', **{'{'+wcm+'}action':'add'})
        add(command,'Order',1); add(command,'Path',WINDOWS_COMMAND); add(command,'WillReboot','Never')
    shell = component(specialize,'Microsoft-Windows-Shell-Setup')
    add(shell,'ComputerName',d['hostname']); add(shell,'TimeZone',d['timezone'])
    if d['rdp']:
        add(component(specialize,'Microsoft-Windows-TerminalServices-LocalSessionManager'),'fDenyTSConnections',False)
        add(component(specialize,'Microsoft-Windows-TerminalServices-RDP-WinStationExtensions'),'UserAuthentication',1)
        firewall = component(specialize,'Networking-MPSSVC-Svc')
        group = add(add(firewall,'FirewallGroups'),'FirewallGroup', **{'{'+wcm+'}action':'add'})
        for tag,value in [('Active',True),('Group','@FirewallAPI.dll,-28752'),('Profile','domain,private'),('Key','RemoteDesktop')]: add(group,tag,value)
    oobe = add(root,'settings', **{'pass':'oobeSystem'})
    intl = component(oobe,'Microsoft-Windows-International-Core')
    for tag,field in [('InputLocale','keyboard'),('SystemLocale','locale'),('UILanguage','ui_language'),('UserLocale','locale')]: add(intl,tag,d[field])
    shell = component(oobe,'Microsoft-Windows-Shell-Setup')
    if d.get('admin_password'):
        password = add(add(shell,'UserAccounts'),'AdministratorPassword')
        add(password,'Value','[REDACTED]' if preview else d['admin_password']); add(password,'PlainText',True)
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def answer_iso(settings, path, bootstrap=None):
    import pycdlib
    xml = answer_xml(settings, bootstrap=bootstrap)
    iso = pycdlib.PyCdlib(); iso.new(interchange_level=3, joliet=3, vol_ident='PROXMATE_SETUP')
    stream = io.BytesIO(xml)
    try:
        iso.add_fp(stream, len(xml), iso_path='/AUTOUNATTEND.XML;1', joliet_path='/Autounattend.xml')
        if bootstrap and bootstrap.get('enabled'):
            from bootstrap import windows_script
            script = windows_script(bootstrap).encode('utf-8-sig')
            script_stream = io.BytesIO(script)
            iso.add_fp(script_stream,len(script),iso_path='/BOOTSTRAP.PS1;1',joliet_path='/ProxmateBootstrap.ps1')
        iso.write(str(path))
    finally: iso.close()


def checksum_for(manifest, filename):
    matches = []
    for line in manifest.splitlines():
        bsd = re.fullmatch(r'SHA256 \((.+)\) = ([a-fA-F0-9]{64})', line.strip())
        gnu = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)', line.strip())
        if bsd and bsd[1] == filename: matches.append(bsd[2])
        if gnu and gnu[2] == filename: matches.append(gnu[1])
    if len(set(matches)) != 1: raise ValueError('No unambiguous SHA256 checksum for selected image')
    return matches[0].lower()
