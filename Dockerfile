FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt ansible-core==2.19.9
COPY collections.yml ./
RUN ansible-galaxy collection install -r collections.yml -p /usr/share/ansible/collections
ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
COPY app.py job_queue.py machine.py operations.py provisioning.py management.py guest_tasks.py bootstrap.py lifecycle.py server_connection.py simple_flow.py media.py template_finish.py windows_bootstrap.ps1 ./
COPY static ./static
RUN useradd --uid 10001 --create-home proxmate && mkdir /data && chown proxmate /data
USER proxmate
ENV DATA_DIR=/data ISO_DIR=/isos
EXPOSE 8080
CMD ["python", "app.py"]
