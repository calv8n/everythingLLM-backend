#!/usr/bin/env bash
# One-time provisioning script for an Oracle Cloud Always Free ARM instance
# (Ubuntu 22.04/24.04 a1.Flex, 4 OCPU / 24 GB — also fine on 1 OCPU / 6 GB).
# Run as the default "ubuntu" user:  bash deploy/provision-oci.sh
set -euo pipefail

echo "==> Installing Docker + git + unattended upgrades..."
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git unattended-upgrades

echo "==> Adding Docker's official apt repo (provides arm64 packages)..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

echo "==> Giving $(whoami) docker access..."
sudo usermod -aG docker "$USER"

echo "==> Pre-pulling sandbox runtime image (saves ~30s on first task)..."
sudo docker pull python:3.11-slim
sudo docker tag python:3.11-slim python:3.11-slim-arm64 || true

echo "==> Basic host hardening..."
# SSH: key-only auth (make sure you are already logged in with a key!)
sudo sed -ri 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload ssh || sudo systemctl reload sshd || true
# Firewall: only SSH + HTTP/HTTPS (OCI Security Lists/NSGs must also open 80/443)
if ! command -v ufw >/dev/null; then sudo apt-get install -y ufw; fi
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo
echo "Done. LOG OUT and back in once so the docker group applies, then run:"
echo "    bash deploy/deploy.sh"
