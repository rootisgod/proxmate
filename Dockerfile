FROM node:24-alpine AS editor
WORKDIR /build
COPY package*.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY editor ./editor
RUN npm run build:editor

FROM scratch AS pwsh-amd64
ADD --checksum=sha256:ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell-7.6.6-linux-x64.tar.gz /pwsh.tar.gz
FROM scratch AS pwsh-arm64
ADD --checksum=sha256:924829e54c983648f6f1419a2dc7f9433c861b2fb5bd57736ff096c24f133729 https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell-7.6.6-linux-arm64.tar.gz /pwsh.tar.gz
FROM pwsh-${TARGETARCH} AS pwsh-files

FROM python:3.12-slim-trixie
RUN dpkg --add-architecture amd64 && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client sshpass xorriso grub-common \
    && cd /tmp && apt-get download grub-pc-bin:amd64 \
    && dpkg-deb --fsys-tarfile grub-pc-bin_*.deb | tar -x -C / ./usr/lib/grub/i386-pc \
    && rm -rf grub-pc-bin_*.deb /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt ansible-core==2.19.9
COPY collections.yml ./
RUN ansible-galaxy collection install -r collections.yml -p /usr/share/ansible/collections
RUN --mount=type=bind,from=pwsh-files,source=/pwsh.tar.gz,target=/tmp/pwsh.tar.gz \
    apt-get update && apt-get install -y --no-install-recommends libicu76 && rm -rf /var/lib/apt/lists/* \
    && mkdir /opt/powershell && tar -xzf /tmp/pwsh.tar.gz -C /opt/powershell \
    && chmod 755 /opt/powershell/pwsh && ln -s /opt/powershell/pwsh /usr/local/bin/pwsh
ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
COPY script_library.py windows_setup.py diagnostics.py reviews.py windows_artifacts.py windows_build.py winrm_setup.py app.py job_queue.py machine.py operations.py provisioning.py management.py guest_tasks.py bootstrap.py lifecycle.py server_connection.py simple_flow.py media.py template_finish.py windows_volume.ps1 windows_bootstrap.ps1 ./
COPY ubuntu_build.py ./
COPY static ./static
COPY --from=editor /build/static/editor-assets ./static/editor-assets
RUN useradd --uid 10001 --create-home proxmate && mkdir /data && chown proxmate /data
USER proxmate
ENV DATA_DIR=/data ISO_DIR=/isos
EXPOSE 8080
CMD ["python", "app.py"]
