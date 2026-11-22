#!/usr/bin/env bash
# GitHub Actions stamps these three values into the downloadable release script.
set -euo pipefail
release_tag=source
archive_sha=unpublished
revision=unpublished
label_format='{{index .Config.Labels "org.opencontainers.image.version"}}@{{index .Config.Labels "org.opencontainers.image.revision"}}'
assets="https://github.com/rootisgod/proxmate/releases/download/$release_tag"

fail() { printf '%s\n' "$*" >&2; exit 1; }

check_manifest() {
    grep -Eq '^    image: ghcr\.io/rootisgod/proxmate:[0-9]+\.[0-9]+\.[0-9]+@sha256:[a-f0-9]{64}[[:space:]]*$' "$1" ||
        fail 'Expected a digest-pinned Proxmate release compose.yaml.'
}

fetch() { curl --fail --location --silent --show-error --retry 3 --proto '=https' --proto-redir '=https' "$assets/$1" -o "$2"; }

prepare_image() {
    local file=$1 scratch=$2 local_ref
    check_manifest "$file"
    docker compose -f "$file" config --quiet
    local_ref="proxmate-local:$archive_sha"
    if [[ $release_tag == source || $(docker image inspect --format "$label_format" "$local_ref" 2>/dev/null || true) != "$release_tag@$revision" ]]; then
        if docker compose -f "$file" pull; then return; fi
        [[ $release_tag != source && $archive_sha =~ ^[a-f0-9]{64}$ && $revision =~ ^[a-f0-9]{40}$ ]] ||
            fail 'Image pull failed. Use the installer from a published release for the verified image download.'
        echo 'Downloading the same tested image from the GitHub release.'
        fetch proxmate-linux-amd64.tar.gz "$scratch/image.tar.gz"
        printf '%s  %s\n' "$archive_sha" "$scratch/image.tar.gz" | sha256sum --check --status ||
            fail 'Release image checksum mismatch. Retry the download.'
        docker load --input "$scratch/image.tar.gz"
        [[ $(docker image inspect --format "$label_format" "proxmate-bundle:$release_tag") == "$release_tag@$revision" ]] ||
            fail 'Loaded image does not match the release.'
        docker tag "proxmate-bundle:$release_tag" "$local_ref"
    fi
    sed -i "s|^    image: .*|    image: $local_ref\n    pull_policy: never|" "$file"
}

update_guest() {
    [[ $release_tag != source ]] || fail 'Download the updater from a published release.'
    cd "${PROXMATE_INSTALL_DIR:-/opt/proxmate}" || fail 'Run the update command inside the existing Proxmate LXC.'
    exec 9>.update.lock
    flock -n 9 || fail 'Another Proxmate update is in progress.'
    [[ -r .env.admin && -f compose.yaml ]] || fail 'Expected the installed dashboard and its existing .env.admin.'
    local running state
    running=$(docker compose ps -q proxmate)
    [[ -n $running ]] || fail 'The installed dashboard must be running so its queue can be checked.'
    if [[ $(docker inspect --format "$label_format" "$running") == "$release_tag@$revision" ]]; then
        echo "Already running $release_tag."
        return
    fi
    scratch=$(mktemp -d)
    next=$(mktemp .compose-next.XXXXXX.yaml)
    trap 'rm -f -- "$next" "$scratch/image.tar.gz"; rmdir -- "$scratch"' EXIT
    fetch compose.yaml "$next"
    prepare_image "$next" "$scratch"
    echo 'Checking the dashboard queue. Keep the pre-update CT backup for recovery.'
    # Hold the existing database write lock across shutdown. This also works on v0.6.0
    # and prevents a new submission or scheduled job slipping past the idle check.
    coproc GUARD { docker compose run --no-deps --rm -T --entrypoint python proxmate -u -c '
import sqlite3, sys
with sqlite3.connect("file:/data/jobs.sqlite?mode=rw", uri=True, timeout=10) as db:
    db.execute("BEGIN IMMEDIATE")
    count = db.execute("SELECT count(*) FROM jobs WHERE status IN (?,?,?)", ("queued","running","waiting")).fetchone()[0]
    print("busy" if count else "idle", flush=True)
    if not count:
        sys.stdin.readline()
'; }
    local guard_pid=$GUARD_PID input_fd=${GUARD[1]} output_fd=${GUARD[0]}
    read -r state <&"$output_fd" || fail 'Queue check failed. The dashboard is still running.'
    [[ $state == idle ]] || fail 'Jobs are queued, running or waiting. Let Activity finish, then retry.'
    cp -p compose.yaml compose.previous.yaml
    docker compose stop proxmate
    printf '\n' >&"$input_fd"
    wait "$guard_pid"
    mv -- "$next" compose.yaml
    if ! docker compose up -d --wait --wait-timeout 120 proxmate; then
        fail 'Startup failed. Run docker compose logs --tail=100 proxmate in /opt/proxmate. Previous settings are in compose.previous.yaml. See the README recovery steps.'
    fi
    echo "Proxmate $release_tag is healthy. Check Inventory at your usual URL."
}

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
    scratch=$(mktemp -d)
    trap 'rm -f -- "$scratch/image.tar.gz" "${admin_tmp:-$scratch/password.tmp}"; rmdir -- "$scratch"' EXIT
    if grep -q '^    image: proxmate-local:' compose.yaml; then
        [[ $(docker image inspect --format "$label_format" "$(docker compose config --images)") == "$release_tag@$revision" ]] || fail 'Restore the release Compose file to retry the image download.'
    else
        prepare_image compose.yaml "$scratch"
    fi
    install -d -m 0755 isos
    if [[ ! -e .env.admin ]]; then
        admin_tmp=$(mktemp .env.admin.XXXXXX)
        openssl rand -hex 24 > "$admin_tmp"
        chown 10001:10001 "$admin_tmp"
        chmod 0400 "$admin_tmp"
        mv -- "$admin_tmp" .env.admin
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
    --update) update_guest; exit ;;
    --help) echo 'Proxmox node: install-proxmox-lxc.sh [--plan]. Existing Proxmate LXC: install-proxmox-lxc.sh --update'; exit ;;
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
if [[ $release_tag != source ]]; then
    compose=$(mktemp)
    trap 'rm -f -- "$compose"' EXIT
    fetch compose.yaml "$compose"
fi
check_manifest "$compose"

disk_store=$(choose_storage rootdir local-lvm "${PROXMATE_CT_STORAGE:-}" PROXMATE_CT_STORAGE)
template_store=$(choose_storage vztmpl local "${PROXMATE_TEMPLATE_STORAGE:-}" PROXMATE_TEMPLATE_STORAGE)
bridge=${PROXMATE_BRIDGE:-vmbr0}
ip link show dev "$bridge" >/dev/null 2>&1 || fail "Bridge $bridge is unavailable. Set PROXMATE_BRIDGE to your management LAN bridge."
ctid=$(pvesh get /cluster/nextid)
[[ $ctid =~ ^[1-9][0-9]{2,8}$ ]] || fail 'Proxmox did not return an available CT ID.'
printf 'Create CT %s (proxmate) on %s: Debian 13, unprivileged, 2 cores, 2 GiB RAM, 32 GiB on %s.\n' "$ctid" "$(hostname)" "$disk_store"
printf 'Templates: %s. Network: DHCP on %s. Start at boot: yes.\n' "$template_store" "$bridge"
[[ ${1:-} == --plan ]] && exit 0
[[ -t 0 ]] || fail 'Run interactively, or use ssh -t, to confirm the new LXC.'
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
