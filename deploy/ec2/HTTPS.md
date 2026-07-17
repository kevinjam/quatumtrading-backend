# Fix mixed content (HTTPS site → HTTP API blocked by Chrome)

Your frontend is `https://www.quatumtrading.com`.
The API must also be HTTPS: `https://api.quatumtrading.com`.

Certbot / Let's Encrypt is **free**.

## On the EC2 server

### 1) Security group
Open inbound **443** (and keep **80** for certificate renewal).

### 2) DNS
`api.quatumtrading.com` A record → your Elastic IP (must already resolve).

### 3) Install certbot + enable HTTPS

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Make sure the HTTP nginx site is active first (for ACME challenge)
sudo cp /opt/quant-api/deploy/ec2/nginx-api-http-only.conf /etc/nginx/sites-available/quant-api
sudo ln -sf /etc/nginx/sites-available/quant-api /etc/nginx/sites-enabled/quant-api
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# Get a free certificate (this rewrites nginx for HTTPS automatically)
sudo certbot --nginx -d api.quatumtrading.com
```

Follow the prompts (email, agree to ToS). Certbot will configure SSL and renewals.

Test:

```bash
curl https://api.quatumtrading.com/api/health
```

### 4) Update `/opt/quant-api/.env`

```env
FRONTEND_URL=https://www.quatumtrading.com
CORS_ORIGINS=https://www.quatumtrading.com,https://quatumtrading.com
GOOGLE_REDIRECT_URI=https://api.quatumtrading.com/api/auth/google/callback
COOKIE_SECURE=true
COOKIE_SAMESITE=none
```

```bash
sudo systemctl restart quant-api
```

### 5) Google Cloud Console
OAuth client → Authorized redirect URIs → add:

`https://api.quatumtrading.com/api/auth/google/callback`

(You can keep the old localhost one for local dev.)

### 6) Frontend (Vercel)
Environment variable:

```env
REACT_APP_BACKEND_URL=https://api.quatumtrading.com
```

Redeploy the frontend after changing it.

Then try login again at `https://www.quatumtrading.com`.
