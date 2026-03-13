FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY gwanjong_mcp/ gwanjong_mcp/

RUN pip install --no-cache-dir ".[all]"

# MCP server (stdio) by default; override CMD for daemon mode
CMD ["gwanjong-mcp"]
