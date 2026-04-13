#!/bin/bash
set -e # Exit immediately if any command fails

echo "Starting LLMStudio Setup via cloud-init..."

# 1. Update system and install basic dependencies (Python, pip, git)
apt update -y
apt upgrade -y
apt install -y python3 python3-pip git build-essential ffmpeg

curl -fsSL https://lmstudio.ai/install.sh | bash

lms login --with-pre-authenticated-keys \
  --key-id ${lms_key_id} \
  --public-key ${lms_public_key} \
  --private-key ${lms_private_key}

echo "Setting up Systemd unit file..."
cat << UNIT > /etc/systemd/system/llmster.service
[Unit]
Description=LM Studio Server

[Service]
Type=oneshot
RemainAfterExit=yes
User=${vm_user}
Environment="HOME=/home/${vm_user}"
ExecStartPre=/home/${vm_user}/.lmstudio/bin/lms daemon up
ExecStart=/home/${vm_user}/.lmstudio/bin/lms server start
ExecStop=/home/${vm_user}/.lmstudio/bin/lms daemon down

[Install]
WantedBy=multi-user.target
UNIT

# Enable and start the service
systemctl daemon-reload
systemctl enable llmster.service
systemctl start llmster.service
echo "Setup complete. LLMStudio should now be running as a systemd service."
