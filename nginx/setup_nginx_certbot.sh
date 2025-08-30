#!/bin/bash
set -e

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: $0 <domain> <email>"
    exit 1
fi

sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

# Copy nginx config and enable site
sudo mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
sudo cp "$(dirname "$0")/sites-available/newserver.conf" /etc/nginx/sites-available/newserver.conf
sudo ln -sf /etc/nginx/sites-available/newserver.conf /etc/nginx/sites-enabled/newserver.conf
sudo sed -i "s/your_domain/$DOMAIN/g" /etc/nginx/sites-available/newserver.conf

sudo nginx -t
sudo systemctl reload nginx

# Obtain certificate and enable HTTPS
sudo certbot --nginx -d "$DOMAIN" -m "$EMAIL" --non-interactive --agree-tos --redirect
