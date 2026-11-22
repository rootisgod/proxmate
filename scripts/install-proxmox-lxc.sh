#!/usr/bin/env bash
# Run on the destination Proxmox node, with the release compose.yaml alongside this file.
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

install_in_guest() {
    [[ $(systemd-detect-virt --container) == lxc ]] || fail 'Guest setup requires an LXC.'
    . /etc/os-release
    [[ $ID == debian && $VERSION_ID == 13 ]] || fail 'Guest setup requires Debian 13.'
    cd /opt/proxmate
    [[ -f compose.yaml ]] || fail 'Place the release compose.yaml in /opt/proxmate first.'
    export HOME=/root DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=5 -o APT::Update::Error-Mode=any update
    apt-get -o Acquire::Retries=5 install -y docker.io docker-cli docker-compose ca-certificates curl openssl
    systemctl enable --now docker
    docker info >/dev/null
    docker compose config --quiet
    printf '%s\n' 'Sign in to ghcr.io with a GitHub account that can read the Proxmate package.'
    read -rp 'GitHub username: ' github_user
    [[ -n $github_user ]] || fail 'Enter a GitHub username.'
    echo 'At the Docker password prompt, paste your classic token with read:packages.'
    docker login ghcr.io --username "$github_user"
    docker compose pull
    install -d -m 0755 isos
    if [[ ! -e .env.admin ]]; then
        admin_tmp=$(mktemp .env.admin.XXXXXX)
        trap 'rm -f -- "$admin_tmp"' EXIT
        openssl rand -hex 24 > "$admin_tmp"
        chown 10001:10001 "$admin_tmp"
        chmod 0400 "$admin_tmp"
        mv -- "$admin_tmp" .env.admin
        trap - EXIT
    fi
    docker compose up -d --wait --wait-timeout 120
    curl -fsS http://127.0.0.1:8088/api/health
    echo
}

choose_storage() {
    local content=$1 preferred=$2 requested=$3 setting=$4 active candidate
    local -a choices=()
    active=$(pvesm status --content "$content" | awk '$3 == "active" { print $1 }')
    if [[ -n $active ]]; then mapfile -t choices <<< "$active"; fi
    for candidate in "${choices[@]}"; do
        if [[ $candidate == "${requested:-$preferred}" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    if [[ -z $requested && ${#choices[@]} == 1 ]]; then
        printf '%s\n' "${choices[0]}"
        return
    fi
    fail "Set $setting to an active $content storage on this node. Available: ${choices[*]:-none}"
}

[[ $EUID == 0 ]] || fail 'Run as root.'
case "${1:-}" in
    --guest) install_in_guest; exit ;;
    --help) echo 'Usage: bash install-proxmox-lxc.sh [--plan]'; exit ;;
    ''|--plan) ;;
    *) fail 'Usage: bash install-proxmox-lxc.sh [--plan]' ;;
esac
[[ $# -le 1 ]] || fail 'Usage: bash install-proxmox-lxc.sh [--plan]'
[[ $(dpkg --print-architecture) == amd64 ]] || fail 'The released image requires an amd64 Proxmox node.'
for cmd in pct pvesh pvesm pveam ip; do
    command -v "$cmd" >/dev/null || fail "Missing $cmd. Run the installer in the Proxmox node shell."
done
script=$(realpath -- "${BASH_SOURCE[0]}")
compose=$(dirname -- "$script")/compose.yaml
[[ -r $compose ]] || fail 'Download the release compose.yaml next to the installer.'
grep -Eq '^    image: ghcr\.io/rootisgod/proxmate:[0-9]+\.[0-9]+\.[0-9]+@sha256:[a-f0-9]{64}[[:space:]]*$' "$compose" ||
    fail 'Use compose.yaml from the GitHub release assets. It must pin the Proxmate image digest.'

disk_store=$(choose_storage rootdir local-lvm "${PROXMATE_CT_STORAGE:-}" PROXMATE_CT_STORAGE)
template_store=$(choose_storage vztmpl local "${PROXMATE_TEMPLATE_STORAGE:-}" PROXMATE_TEMPLATE_STORAGE)
bridge=${PROXMATE_BRIDGE:-vmbr0}
ip link show dev "$bridge" >/dev/null 2>&1 || fail "Bridge $bridge is unavailable. Set PROXMATE_BRIDGE to your management LAN bridge."
ctid=$(pvesh get /cluster/nextid)
[[ $ctid =~ ^[1-9][0-9]{2,8}$ ]] || fail 'Proxmox did not return an available CT ID.'
printf 'Create CT %s (proxmate) on %s: Debian 13, unprivileged, 2 cores, 2 GiB RAM, 32 GiB on %s.\n' "$ctid" "$(hostname)" "$disk_store"
printf 'Templates: %s. Network: DHCP on %s. Start at boot: yes.\n' "$template_store" "$bridge"
[[ ${1:-} == --plan ]] && exit 0
[[ -t 0 ]] || fail 'Run interactively, or use ssh -t, for the creation and registry prompts.'
read -rp 'Create this new container? [y/N] ' answer
[[ $answer == [yY] ]] || { echo 'Cancelled.'; exit 0; }

# Reuse a cached Debian 13 template before downloading another copy.
pattern='^debian-13-standard_[^/[:space:]]+_amd64\.tar\.(zst|gz|xz)$'
template=$(pveam list "$template_store" | awk -v re="$pattern" '{n=split($1,a,"/"); if (a[n] ~ re) print a[n]}' | sort -V | tail -n 1)
if [[ -z $template ]]; then
    pveam update
    template=$(pveam available --section system | awk -v re="$pattern" '$2 ~ re {print $2}' | sort -V | tail -n 1)
    [[ -n $template ]] || fail 'No Debian 13 standard amd64 template found in the Proxmox catalog.'
    pveam download "$template_store" "$template"
fi

pct create "$ctid" "$template_store:vztmpl/$template" --hostname proxmate --ostype debian \
    --unprivileged 1 --features nesting=1,keyctl=1 --cores 2 --memory 2048 \
    --rootfs "$disk_store:32" --net0 "name=eth0,bridge=$bridge,ip=dhcp" --onboot 1
echo "Created CT $ctid. It will be kept if setup stops."
pct start "$ctid"
pct exec "$ctid" -- install -d -m 0700 /opt/proxmate
pct push "$ctid" "$compose" /opt/proxmate/compose.yaml --perms 0600
pct push "$ctid" "$script" /opt/proxmate/install-proxmox-lxc.sh --perms 0700
echo "To retry setup in this CT: pct exec $ctid --keep-env 0 -- bash /opt/proxmate/install-proxmox-lxc.sh --guest"
pct exec "$ctid" --keep-env 0 -- bash /opt/proxmate/install-proxmox-lxc.sh --guest
address=$(pct exec "$ctid" -- ip -4 -o address show dev eth0 scope global | awk 'NR==1 {split($4,ip,"/"); print ip[1]}')
printf 'Dashboard: http://%s:8088\n' "${address:-CT-IP}"
printf 'Read the dashboard password in the Proxmox shell: pct exec %s -- cat /opt/proxmate/.env.admin\n' "$ctid"
