# CI/CD Plan: GitHub Actions → GitHub Container Registry (ghcr.io)

## Goal
Automatically build and push the `odoo-mcp` Docker image to `ghcr.io` on:
- Every push to `main` → tagged as `latest`
- Every version tag (`v*.*.*`) → tagged as semver (e.g. `v1.2.3`, `1.2`, `1`)

---

## Workflow File Location
```
odoo-mcp/
└── .github/
    └── workflows/
        └── docker-publish.yml
```

---

## Full Workflow: `.github/workflows/docker-publish.yml`

```yaml
name: Build and Push Docker Image

on:
  push:
    branches:
      - main
  workflow_dispatch: # allow manual trigger

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    runs-on: ubuntu-latest

    permissions:
      contents: read
      packages: write
      attestations: write
      id-token: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v5

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker metadata (tags + labels)
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push Docker image
        id: push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Generate artifact attestation
        uses: actions/attest-build-provenance@v3
        with:
          subject-name: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          subject-digest: ${{ steps.push.outputs.digest }}
          push-to-registry: true
```

---

## How Tagging Works

| Event | Resulting Image Tags |
|---|---|
| Push to `main` | `ghcr.io/<owner>/odoo-mcp:main`, `ghcr.io/<owner>/odoo-mcp:latest` |
| Manual trigger | same as push to main |

---

## Required Repository Setup

1. **No secrets needed** — `GITHUB_TOKEN` is automatically available in Actions
2. **Package visibility**: Go to `ghcr.io/<owner>/odoo-mcp` → Settings → Change visibility to Public (or keep Private for restricted access)
3. **First push**: GitHub auto-creates the package on first successful build

---

## Pulling the Published Image

```bash
# Pull latest
docker pull ghcr.io/<github-username>/odoo-mcp:latest

# Pull specific version
docker pull ghcr.io/<github-username>/odoo-mcp:1.2.3
```

---

## Using Published Image in docker-compose.yaml

Instead of building locally, pull from registry:

```yaml
services:
  odoo-mcp:
    image: ghcr.io/<github-username>/odoo-mcp:latest
    ports:
      - "8080:8080"
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/mcp"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

For production, pin to a specific tag instead of `latest`:
```yaml
image: ghcr.io/<github-username>/odoo-mcp:1.2.3
```

---

## Build Cache Strategy

`cache-from: type=gha` + `cache-to: type=gha,mode=max` uses GitHub Actions Cache.
This makes rebuilds fast — the Tesseract/system-packages layer and deps layer are
cached between runs. Only layers that changed (e.g. application code) are rebuilt.

The two-phase uv Dockerfile install pattern (deps first, code second) maximizes
this: code changes only rebuild the final layer, not the heavy apt/uv deps layer.

---

## Local docker-compose.yaml for Development (build locally)

```yaml
services:
  odoo-mcp:
    build: .          # build from local Dockerfile
    ports:
      - "8080:8080"
    env_file: .env
    restart: unless-stopped
```

## Remote/Production docker-compose.yaml (pull from GHCR)

```yaml
services:
  odoo-mcp:
    image: ghcr.io/<github-username>/odoo-mcp:latest
    ports:
      - "8080:8080"
    env_file: .env
    restart: unless-stopped
```

---

## Deployment Flow

```
Developer pushes to main
        │
        ▼
GitHub Actions triggers
        │
        ├─ docker/setup-buildx-action  (multi-platform builder)
        ├─ docker/login-action         (authenticate to ghcr.io)
        ├─ docker/metadata-action      (generate :main and :latest tags)
        ├─ docker/build-push-action    (build + push, GHA cache)
        └─ attest-build-provenance     (supply chain attestation)
        │
        ▼
ghcr.io/<owner>/odoo-mcp:main
ghcr.io/<owner>/odoo-mcp:latest
        │
        ▼
Production server: docker compose pull && docker compose up -d
```
