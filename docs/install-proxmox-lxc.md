# Install Proxmate in a Debian LXC

Use this route for a dedicated dashboard on an existing Proxmox VE host. You need an Intel or AMD 64-bit host, a LAN address for the dashboard, and permission to read the private GitHub release and its GHCR package. Guests still run on Proxmox. Only the dashboard and its worker run inside the container.

An unprivileged LXC is lightweight and can start with the host. A small Debian VM is also a good choice if your cluster restricts nested Docker or you need live migration and stronger isolation. The installation from step 2 is the same. [Proxmox Container Toolkit](https://github.com/proxmox/pve-docs/blob/master/pct.adoc)

## 1. Create the system container

1. In the Proxmox web UI, open a node's template-capable storage, choose **CT Templates → Templates**, and download the Debian 13 standard amd64 template.
2. Choose **Create CT**, pick an unused CT ID, and name it `proxmate`. Keep **Unprivileged container** enabled. A starting allocation for a small lab is 2 vCPUs, 2 GiB RAM and a 32 GiB root disk. Allow extra disk space if you keep large ISOs locally.
3. Select your management LAN bridge. Use a static address or DHCP reservation. The dashboard needs access to GitHub and GHCR for installation, the Proxmox API on TCP 8006, and the hosts and guest management ports used by your builds.
4. Leave the CT stopped at the end of the wizard. Under **Options → Features**, enable **Nesting** and **keyctl**. Enable **Start at boot**, start the CT, and open its **Console**. Run the remaining commands as root **inside this CT**. [Proxmox LXC feature definitions](https://github.com/proxmox/pve-container/blob/master/src/PVE/LXC/Config.pm)

## 2. Install Docker

On a fresh Debian 13 CT, add Docker's Debian repository and install Engine and the Compose plugin.

```sh
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version
docker run --rm hello-world
```

Continue once `hello-world` succeeds. If Docker reports a mount, sysctl or AppArmor permission error, check the two CT features, installed Proxmox updates and the CT's storage configuration. A Debian VM provides a fallback with the standard security profiles. [Docker Engine on Debian](https://docs.docker.com/engine/install/debian/)

## 3. Install Proxmate

On a Linux Docker host, start here with rootful Docker Engine 25 or later and Compose 2.20.2 or later. The examples use Bash. Create the directory that will hold your install settings.

```sh
install -d -m 0700 /opt/proxmate
cd /opt/proxmate
install -d -m 0755 isos
```

Download the **compose.yaml** asset from the chosen release into this directory. You can transfer it from a signed-in browser or use the GitHub CLI inside the CT. The CLI's browser login displays a device code you can enter on your workstation.

```sh
apt-get install -y gh openssl
gh auth login --hostname github.com --git-protocol https --web
gh release download --repo rootisgod/proxmate --pattern compose.yaml
```

Without a tag, this selects the latest stable release. To install a particular release, add its tag after `download`, for example `v0.6.0`. The asset contains the exact image digest. [Proxmate releases](https://github.com/rootisgod/proxmate/releases)

Sign in to GHCR using your GitHub username. At Docker's password prompt, paste a personal access token (classic) with **read:packages** from an account granted read access to the package. Keep the dashboard password separate. [GitHub Container Registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic)

```sh
read -rp 'GitHub username: ' github_user
docker login ghcr.io --username "$github_user"
docker compose pull
```

Generate the dashboard password on the first installation. The Proxmate process runs as UID 10001 inside Docker and needs to read this one file. Store the displayed password in your password manager.

```sh
if [ ! -e .env.admin ]; then
  (umask 077 && openssl rand -hex 24 > .env.admin)
  chown 10001:10001 .env.admin
  chmod 0400 .env.admin
fi
cat .env.admin
docker compose up -d --wait
curl -fsS http://127.0.0.1:8088/api/health
```

The health response includes `"status":"ok"`. Open **http://CT-IP:8088** from your LAN and sign in. To choose another host port, set `PORT=8090` in `/opt/proxmate/.env` before startup and use that port in the browser and health check. Keep this administrator dashboard on a trusted LAN or behind a protected HTTPS proxy.

## 4. Connect your cluster

Open **Server settings** in Proxmate. Supply the Proxmox HTTPS address, realm-qualified username and password. Choose **Test connection**, then **Save & connect**. Open **Inventory** and check that you see the expected guests. For builds that prepare installation media over SSH, also configure the SSH user and the node's independently verified host-key fingerprint.

Start with installer media already on Proxmox storage. **ISO Library** discovers it through the connected node. Optionally put extra files under `/opt/proxmate/isos` or set `ISO_PATH=/another/readable/path` in `.env` and recreate the service. This directory must exist inside the CT and be readable by UID 10001. Compose mounts it read-only. Proxmate settings, credentials, scripts and job history persist in the named Docker volume mounted at `/data`.

## Upgrade or roll back

On dashboard versions with **Resources → Backup & restore**, a **Complete dashboard** download gives you a separate application-level recovery copy. A **Portable configuration** download moves definitions to another cluster. Keep your CT backup for the installation files, dashboard access keys, Docker settings and local ISO media.

1. Check **Activity** for queued, running and waiting jobs. Let them finish and pause recurring schedules and policies during maintenance. Back up the CT root disk, including the Docker data volume, `.env.admin` and the current Compose file. The database and its encryption key belong together. Include any separately mounted storage in your backup plan.
2. From `/opt/proxmate`, download the new Compose asset and pull its image. These commands leave the running service available during the download.

   ```sh
   gh release download --repo rootisgod/proxmate --pattern compose.yaml --output compose.next.yaml
   docker compose -f compose.next.yaml config --images
   docker compose -f compose.next.yaml pull
   ```

3. With the queue idle, switch the file in the same directory, preserving the project name and volume. Verify health, sign in, check **Inventory**, then resume schedules.

   ```sh
   cp -p compose.yaml compose.previous.yaml
   mv compose.next.yaml compose.yaml
   docker compose up -d --wait
   docker compose ps
   ```

4. For an image rollback with a compatible database, copy `compose.previous.yaml` back to `compose.yaml` and run `docker compose up -d --wait`. If the earlier version requires its earlier data format, restore the matching backup first. Preserve the volume throughout. For startup errors use `docker compose logs --tail=100 proxmate`. A GHCR access error usually means the account needs package read permission or a refreshed registry login.
