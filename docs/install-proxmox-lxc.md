# Install Proxmate in a Debian LXC

Use this route for a dedicated dashboard on an existing Proxmox VE host. You need an Intel or AMD 64-bit host, a LAN address for the dashboard, and outbound HTTPS to download the public release. Guests still run on Proxmox. Only the dashboard and its worker run inside the container.

An unprivileged LXC is lightweight and can start with the host. A small Debian VM is also a good choice if your cluster restricts nested Docker or you need live migration and stronger isolation. The installation from step 2 is the same. [Proxmox Container Toolkit](https://github.com/proxmox/pve-docs/blob/master/pct.adoc)

## Install from the Proxmox shell

Follow the [README quickstart](https://github.com/rootisgod/proxmate#quickstart-run-proxmate-on-your-proxmox-server) for the one-command installation. Run the script in a root terminal on the node where you want the container. It selects the next cluster-wide guest ID, uses a cached Debian 13 amd64 template when available, and offers to create a 32 GiB unprivileged LXC with nesting, keyctl and start at boot. Docker runs inside Debian using its packaged Engine and Compose plugin. [Debian Docker Engine](https://packages.debian.org/trixie/docker.io) · [Debian Compose plugin](https://packages.debian.org/trixie/amd64/docker-compose/filelist)

The installer prefers `local-lvm` for the root disk and `local` for the template. If the preferred storage is unavailable, it selects the only eligible active storage or lists the choices. To preview a custom placement from the node shell, substitute your own storage and bridge names:

```sh
PROXMATE_CT_STORAGE=ssd PROXMATE_TEMPLATE_STORAGE=local PROXMATE_BRIDGE=vmbr1 bash /root/proxmate-install.sh --plan
```

Repeat the command without `--plan` when the placement is correct. For a fixed IP, reserve the CT’s DHCP lease or use the manual creation steps below. The installer prints the new ID and a retry command. If the image download, package installation or dashboard startup fails, fix the issue and use that retry command on the existing CT. The host installer always allocates a fresh ID. To inspect the service, enter the CT with `pct enter <CT-ID>` and run `docker compose -f /opt/proxmate/compose.yaml logs --tail=100`. The generated dashboard password and Compose file stay in `/opt/proxmate`, and dashboard data persists in the Docker volume.

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

Download the pinned Compose asset and pull the image. If GHCR requires authentication, use the one-command LXC installer for its verified GitHub release image fallback, or authenticate Docker with an account that has package access.

```sh
curl -fL https://github.com/rootisgod/proxmate/releases/latest/download/compose.yaml -o compose.yaml
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

Follow [Update an existing install](https://github.com/rootisgod/proxmate#update-an-existing-install) for the one-command update, backup, health check and rollback. Use the existing CT throughout.
