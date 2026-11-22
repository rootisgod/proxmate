#!/usr/bin/env bash
# Run as root in the disposable, network-disabled CI container. No Proxmox or Docker socket needed.
set -euo pipefail
[[ $EUID == 0 && -f /.dockerenv && ! -e /opt/proxmate && ! -S /var/run/docker.sock ]] ||
    { echo 'Use a fresh, isolated Docker container for this check.' >&2; exit 1; }
source_script=${1:?Pass the path to install-proxmox-lxc.sh}
tmp=$(mktemp -d)
export CALLS="$tmp/calls" PATH="$tmp/bin:$PATH"
mkdir -p "$tmp/bin"
cp "$source_script" "$tmp/install-proxmox-lxc.sh"
cd "$tmp"
if [[ -n ${2:-} ]]; then
    cp "$2" compose.yaml
else
    printf 'services:\n  proxmate:\n    image: ghcr.io/rootisgod/proxmate:1.2.3@sha256:%064d\n' 0 > compose.yaml
fi

cat > bin/mock <<'MOCK'
#!/usr/bin/env bash
set -eu
name=${0##*/}
echo "$name $*" >> "$CALLS"
case $name in
  pvesh) echo 321 ;;
  pvesm)
    if [[ $3 == rootdir ]]; then
      printf 'local-lvm lvmthin active\nssd zfs active\ncold dir inactive\n'
    else echo 'local dir active'; fi ;;
  pveam)
    case $1 in
      list) [[ ${CACHED:-no} != yes ]] || echo 'local:vztmpl/debian-13-standard_13.1-1_amd64.tar.zst 120MB' ;;
      available) printf '%s\n' 'system debian-13-standard_13.1-1_amd64.tar.zst' 'system debian-13-standard_13.10-1_amd64.tar.zst' ;;
    esac ;;
  pct)
    if [[ $1 == create && ${OCCUPIED:-no} == yes ]]; then exit 17; fi
    if [[ $1 == exec && $* == *'address show dev eth0'* ]]; then echo '2: eth0 inet 192.0.2.40/24 scope global eth0'; fi ;;
  systemd-detect-virt) echo lxc ;;
  curl)
    if [[ $* == *' -o '* ]]; then
      if [[ $* == *'.tar.gz '* ]]; then printf '%s' "${ARCHIVE:-fixture}" > "${!#}"; else cp "$FIXTURE_MANIFEST" "${!#}"; fi
    fi ;;
  docker)
    if [[ $1 == login ]]; then read -r ignored; fi ;;
esac
MOCK
chmod +x bin/mock
for tool in pvesh pvesm pveam pct ip systemd-detect-virt apt-get systemctl docker curl; do ln -s mock "bin/$tool"; done

run_host() { bash "$tmp/install-proxmox-lxc.sh" "$@"; }
# script(1) supplies the same interactive stdin as a Proxmox shell.
interactive() { script -qec "bash '$tmp/install-proxmox-lxc.sh' $1" /dev/null; }
: > "$CALLS"
run_host --plan | grep -q '32 GiB on local-lvm'
! grep -qE '^(pct|pveam) ' "$CALLS" || exit 1
if PROXMATE_CT_STORAGE=cold run_host --plan >/dev/null 2>&1; then exit 1; fi
interactive '' <<< n >/dev/null
! grep -q '^pct create' "$CALLS" || exit 1
if OCCUPIED=yes interactive '' <<< y >/dev/null; then exit 1; fi
! grep -q '^pct start' "$CALLS" || exit 1

: > "$CALLS"
PROXMATE_CT_STORAGE=ssd interactive '' <<< y > completed
grep -q 'http://192.0.2.40:8088' completed
grep -q '^pveam download local debian-13-standard_13.10-1_amd64.tar.zst' "$CALLS"
grep -q '^pct create 321 local:vztmpl/.*--unprivileged 1 .*--rootfs ssd:32 .*ip=dhcp .*--onboot 1' "$CALLS"
grep -q '^pct exec 321 --keep-env 0 -- bash /opt/proxmate/install-proxmox-lxc.sh --guest' "$CALLS"
: > "$CALLS"
CACHED=yes interactive '' <<< y >/dev/null
! grep -qE '^pveam (update|download)' "$CALLS" || exit 1

# Check guest setup against the real secret ownership and mode, with package/daemon calls stubbed.
mkdir -m 0700 /opt/proxmate
cp compose.yaml /opt/proxmate/
run_host --guest <<< $'example\nexample-token' >/dev/null
admin=/opt/proxmate/.env.admin
[[ $(stat -c '%u:%g:%a' "$admin") == 10001:10001:400 && -s $admin ]]
original=$(sha256sum "$admin")
run_host --guest <<< $'example\nexample-token' >/dev/null
[[ $(sha256sum "$admin") == "$original" ]]
grep -q '^docker compose up -d --wait --wait-timeout 120' "$CALLS"
! grep -q 'example-token' "$CALLS" || exit 1
! grep -q '^docker login' "$CALLS" || exit 1

# The release script fetches its own pinned Compose file and can use a verified
# archive when GHCR is unavailable. Only the asset transport/engine are mocked.
export FIXTURE_MANIFEST="$tmp/compose.yaml"
archive_sha=$(printf fixture | sha256sum | cut -d' ' -f1)
export FIXTURE_REVISION=$(printf '%040d' 5)
sed -e 's/^release_tag=source/release_tag=v1.2.3/' -e "s/^archive_sha=unpublished/archive_sha=$archive_sha/" \
    -e "s/^revision=unpublished/revision=$FIXTURE_REVISION/" "$source_script" > "$tmp/released.sh"
bash "$tmp/released.sh" --plan | grep -q 'Create CT 321'
rm "$tmp/bin/docker"
cat > "$tmp/bin/docker" <<'ENGINE'
#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$*" in
  *' pull'|'image inspect '*proxmate-local:*) exit 1 ;;
  'image inspect '*proxmate-bundle:*) echo "v1.2.3@$FIXTURE_REVISION" ;;
esac
ENGINE
chmod +x "$tmp/bin/docker"
if ARCHIVE=corrupt bash "$tmp/released.sh" --guest >/dev/null 2>&1; then exit 1; fi
! grep -q '^load --input' "$CALLS" || exit 1
bash "$tmp/released.sh" --guest >/dev/null
grep -q '^load --input' "$CALLS"
grep -q '^    pull_policy: never$' /opt/proxmate/compose.yaml
echo 'LXC installer checks passed for preview, storage, occupied IDs, templates, guest setup and retry.'
