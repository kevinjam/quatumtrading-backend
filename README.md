# Quant Trading Analysis API

FastAPI backend. **Free-tier deploy:** Ubuntu EC2 + systemd + GitHub Actions SSH.

## Local

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Health: `GET /api/health`

## Production `.env` (important)

Put it **only on the EC2 server**:

```text
/opt/quant-api/.env
```

Never commit it. GitHub Actions does not upload secrets — it only pulls code and restarts the service.

Full free-tier guide: [`deploy/ec2/README.md`](deploy/ec2/README.md)

## Deploy (free)

| Piece | Cost |
|-------|------|
| EC2 `t2.micro` / `t3.micro` | Free tier |
| MongoDB Atlas M0 | Free |
| GitHub Actions | Free minutes |
| ECS / Fargate / ECR / ALB | **Not used** |

Workflows (this folder = git repo root):

- `.github/workflows/ci.yml` — syntax check
- `.github/workflows/deploy-ec2.yml` — SSH → `git pull` → restart `quant-api`

GitHub secrets: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY` (optional `EC2_PORT`)
