# Suggested Commands — odoo-mcp

## Development
```bash
# Install / sync dependencies
uv sync

# Add a new dependency
uv add <package>

# Run server locally (requires .env)
uv run python main.py

# Run with explicit host/port
uv run python -c "from main import mcp; mcp.run(transport='streamable-http', host='0.0.0.0', port=8080)"
```

## Docker
```bash
# Build
docker compose build

# Build without cache (after code changes)
docker compose build --no-cache

# Start
docker compose up -d

# Logs
docker compose logs -f odoo-mcp

# Restart after config change
docker compose up -d --force-recreate odoo-mcp

# Stop
docker compose down
```

## Verification
```bash
# Check MCP endpoint is live
curl -f http://localhost:8080/mcp

# List registered tools inside container
docker compose exec odoo-mcp uv run python -c "
from main import mcp
import asyncio
async def check():
    tools = await mcp.list_tools()
    for t in tools: print(t.name)
asyncio.run(check())
"
```

## Environment
```bash
# Copy example env file
cp .env.example .env
# Then fill in: ODOO_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY, MCP_SERVER_TOKEN
```
