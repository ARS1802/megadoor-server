#!/usr/bin/env bash
set -euo pipefail

SERVER_IP="${1:-}"

if [[ -z "$SERVER_IP" ]]; then
  echo "Uso: ./scripts/create-cert.sh IP_DO_SERVIDOR"
  echo "Exemplo: ./scripts/create-cert.sh 192.168.1.50"
  exit 1
fi

mkdir -p certs

openssl req \
  -x509 \
  -newkey rsa:4096 \
  -nodes \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 \
  -subj "/CN=$SERVER_IP" \
  -addext "subjectAltName=IP:$SERVER_IP,DNS:localhost,IP:127.0.0.1"

chmod 600 certs/server.key

echo "Certificado criado em certs/server.crt"
echo "Chave privada criada em certs/server.key"
