#!/bin/bash
set -e

DOMAIN=$1
if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <domain>"
    exit 1
fi

CERT_DIR=/etc/letsencrypt/live/$DOMAIN
FIRMWARE_DIR="$(dirname "$0")/../UltraNodeV5/main/certs"

mkdir -p "$FIRMWARE_DIR"

cp "$CERT_DIR/fullchain.pem" "$FIRMWARE_DIR/server.crt"
cp "$CERT_DIR/privkey.pem" "$FIRMWARE_DIR/server.key"

# Convert to C headers for embedding in firmware
xxd -i "$FIRMWARE_DIR/server.crt" > "$FIRMWARE_DIR/server_crt.h"
xxd -i "$FIRMWARE_DIR/server.key" > "$FIRMWARE_DIR/server_key.h"

echo "Certificates copied to $FIRMWARE_DIR and converted to headers."
