# Free-tier: public domain → nginx → uvicorn :8000
#
# Goal:  http://api.quatumtrading.com/api/health
# (HTTPS with Let's Encrypt is free later — see bottom)

## 1) Elastic IP (recommended)
In AWS console → EC2 → Elastic IPs → Allocate → Associate to your instance.
Use that public IP everywhere below (it won't change when you reboot).

## 2) Security group inbound
| Port | Source | Why |
|------|--------|-----|
| 22 | Your IP | SSH |
| 80 | 0.0.0.0/0 | HTTP (domain) |
| 443 | 0.0.0.0/0 | HTTPS (later) |
| 8000 | optional | Direct uvicorn (can close after nginx works) |

## 3) DNS (wherever you bought the domain)
Create an **A record**:

```text
Host:  api
Type:  A
Value: YOUR_ELASTIC_IP
TTL:   300
```

So `api.quatumtrading.com` → your EC2 IP.

Wait a few minutes, then check:

```bash
dig +short api.quatumtrading.com
```

## 4) Install nginx on the EC2 box

```bash
sudo apt-get update
sudo apt-get install -y nginx
sudo cp /opt/quant-api/deploy/ec2/nginx-api.conf /etc/nginx/sites-available/quant-api
sudo ln -sf /etc/nginx/sites-available/quant-api /etc/nginx/sites-enabled/quant-api
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

## 5) Update production `/opt/quant-api/.env`

```env
FRONTEND_URL=https://your-frontend.vercel.app
CORS_ORIGINS=https://your-frontend.vercel.app,http://localhost:3000
GOOGLE_REDIRECT_URI=http://api.quatumtrading.com/api/auth/google/callback
COOKIE_SECURE=false
COOKIE_SAMESITE=lax
```

Then:

```bash
sudo systemctl restart quant-api
```

Also add that same redirect URI in **Google Cloud Console → OAuth client → Authorized redirect URIs**.

## 6) Test from your laptop

```bash
curl http://api.quatumtrading.com/api/health
```

You should see JSON like `{"service":"quant-trading-analysis","status":"ok",...}`.

Frontend env (Vercel):

```env
REACT_APP_BACKEND_URL=http://api.quatumtrading.com
```

## Free HTTPS later (recommended for real login from Vercel)

Browsers often block cookies from HTTPS Vercel → HTTP API. When ready:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.quatumtrading.com
```

Then set:

```env
GOOGLE_REDIRECT_URI=https://api.quatumtrading.com/api/auth/google/callback
COOKIE_SECURE=true
COOKIE_SAMESITE=none
```

and update Google Console + Vercel `REACT_APP_BACKEND_URL=https://api.quatumtrading.com`.
