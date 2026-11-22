#!/usr/bin/env bash
# Exercise the queue guard and image switch against a disposable Compose project.
set -euo pipefail
base_image=${1:?Pass the tested application image}
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
export PROXMATE_INSTALL_DIR="$work" COMPOSE_PROJECT_NAME="proxmate-update-check-${RANDOM}-$$"
export PORT=127.0.0.1:0 ISO_PATH=./isos
revision=$(printf '%040x' "$$")
sha=$(printf '%s' "$work" | sha256sum | cut -d' ' -f1)
candidate="proxmate-local:$sha"
cd "$work"
mkdir isos bin
cleanup() {
    docker compose down --volumes >/dev/null
    docker image rm "$candidate" >/dev/null
    rm -rf -- "$work"
}
trap cleanup EXIT
printf '%s\n' 'temporary test login' > .env.admin
chmod 444 .env.admin
sed "s|^    image: .*|    image: $base_image|" "$root/compose.release.yaml" > compose.yaml
cp compose.yaml original.yaml
docker build --quiet -t "$candidate" --label org.opencontainers.image.version=v0.7.0 \
    --label "org.opencontainers.image.revision=$revision" - <<< "FROM $base_image" >/dev/null
docker compose up -d --wait
service=$(docker compose ps -q proxmate)
docker compose exec -T proxmate python -c '
import sqlite3
with sqlite3.connect("/data/jobs.sqlite") as db:
    db.execute("insert into jobs (id,status) values (?,?)", ("update-check","waiting"))'

# Stage an already verified local image through the exact release manifest format.
sed "s|^    image: .*|    image: ghcr.io/rootisgod/proxmate:0.7.0@sha256:$sha|" "$root/compose.release.yaml" > candidate.yaml
cat > bin/curl <<'CURL'
#!/usr/bin/env bash
set -eu
[[ $* == *'/v0.7.0/compose.yaml '* ]]
cp "$PROXMATE_INSTALL_DIR/candidate.yaml" "${!#}"
CURL
chmod +x bin/curl
export PATH="$work/bin:$PATH"
sed -e 's/^release_tag=source/release_tag=v0.7.0/' -e "s/^archive_sha=unpublished/archive_sha=$sha/" \
    -e "s/^revision=unpublished/revision=$revision/" "$root/scripts/install-proxmox-lxc.sh" > update.sh
if bash update.sh --update > busy.log 2>&1; then exit 1; fi
grep -q 'Jobs are queued, running or waiting' busy.log
cmp original.yaml compose.yaml
[[ $(docker compose ps -q proxmate) == "$service" ]]
docker compose exec -T proxmate python -c '
import sqlite3
with sqlite3.connect("/data/jobs.sqlite") as db:
    db.execute("update jobs set status=? where id=?", ("succeeded","update-check"))'
bash update.sh --update
cmp original.yaml compose.previous.yaml
[[ $(docker compose ps -q proxmate) != "$service" ]]
docker compose exec -T proxmate python -c '
import sqlite3
assert sqlite3.connect("/data/jobs.sqlite").execute("select status from jobs where id=?", ("update-check",)).fetchone() == ("succeeded",)'
bash update.sh --update | grep -q 'Already running v0.7.0'
echo 'Release update passed busy, healthy, persistence and repeat checks.'
