# Free-tier EC2 deploy (no paid AWS services)

This stack stays on the **AWS free tier**:

| Use | Avoid (costs money) |
|-----|---------------------|
| EC2 `t2.micro` / `t3.micro` Ubuntu | ECS Fargate |
| Security group + Elastic IP (free tier eligible) | ECR, Load Balancers, NAT Gateways |
| MongoDB **Atlas M0 free** cluster | DocumentDB / paid RDS |
| GitHub Actions (free minutes) | CodeDeploy / paid CI |

No AWS Marketplace subscriptions required.

---

## Where does production `.env` go?

**Only on the EC2 server**, never in git:

```text
/opt/quant-api/.env
```

Create it once:

```bash
cd /opt/quant-api
cp .env.example .env
nano .env   # paste production secrets
chmod 600 .env
```

systemd loads it via `EnvironmentFile=/opt/quant-api/.env`.

GitHub Actions **does not** upload your `.env`. It only SSHs in, `git pull`s code, reinstalls deps, and restarts the service.

---

## One-time EC2 setup

1. Launch **Ubuntu 22.04/24.04** free-tier instance (`t2.micro` / `t3.micro`).
2. Security group inbound:
   - `22` from your IP (SSH)
   - `8000` from anywhere (or `80`/`443` later with nginx)
3. SSH in and install:

```bash
sudo mkdir -p /opt/quant-api
sudo chown ubuntu:ubuntu /opt/quant-api
git clone <YOUR_BACKEND_REPO_URL> /opt/quant-api
cd /opt/quant-api
cp .env.example .env
nano .env   # set MONGO_URL (Atlas free), Google, FRONTEND_URL, CORS, etc.
chmod +x deploy/ec2/setup.sh
./deploy/ec2/setup.sh
```

4. Confirm: `curl http://YOUR_EC2_PUBLIC_IP:8000/api/health`

### Production `.env` tips (free)

```env
MONGO_URL=mongodb+srv://...   # Atlas free M0
FRONTEND_URL=https://your-app.vercel.app
CORS_ORIGINS=https://your-app.vercel.app
GOOGLE_REDIRECT_URI=http://YOUR_EC2_IP:8000/api/auth/google/callback
# After you add HTTPS later, switch to https://api.yourdomain.com/...
COOKIE_SECURE=false           # true only when API is on HTTPS
COOKIE_SAMESITE=lax           # use none + COOKIE_SECURE=true once on HTTPS
```

Vercel (HTTPS) talking to bare `http://IP:8000` is **cross-site**; browsers may block cookies until you put HTTPS on the API (free: Caddy or nginx + Let’s Encrypt). For local testing you can still use the API; for real Google login from Vercel you’ll want a free TLS cert later.

---

## GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Example |
|--------|---------|
| `EC2_HOST` | `54.x.x.x` or Elastic IP |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | Full private key (`-----BEGIN ...`) |
| `EC2_PORT` | `22` (optional) |
| `EC2_APP_DIR` | `/opt/quant-api` (optional) |

On the EC2 box, add the matching **public** key to `~/.ssh/authorized_keys`.

Push to `main` → workflow SSHs in → pull → restart `quant-api`.

---

## Useful commands on the server

```bash
sudo systemctl status quant-api
sudo journalctl -u quant-api -f
sudo systemctl restart quant-api
```
