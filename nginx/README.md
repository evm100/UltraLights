# Nginx Reverse Proxy and Certificate Deployment

This directory contains helper scripts and configuration for running the `NewServer` Flask app behind an HTTPS reverse proxy and embedding the resulting SSL certificate into the `UltraNodeV5` firmware.

## Setup Nginx and Let's Encrypt

1. Run the setup script with your domain and email:
   ```bash
   ./setup_nginx_certbot.sh example.com admin@example.com
   ```
   - Installs nginx and certbot.
   - Copies the sample site config to `/etc/nginx/sites-available/newserver.conf` and enables it.
   - Requests a Let's Encrypt certificate for `example.com` and configures nginx to use it.

2. Ensure the `NewServer` Flask app is running locally on port 5000.

## Deploy certificate to firmware

After a certificate has been issued, copy it into the firmware tree and generate C header files:

```bash
./deploy_cert_to_firmware.sh example.com
```

The PEM files and generated headers are placed in `UltraNodeV5/main/certs/`.

Include the headers in your firmware code to establish secure connections.

## Nginx config

- `sites-available/newserver.conf` – base nginx configuration that proxies requests to the Flask app.
- `sites-enabled/newserver.conf` – symlink to the enabled site.

Edit `newserver.conf` to adjust additional nginx settings if needed.
