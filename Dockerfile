# Stage 1: Build stage
FROM python:3.11-slim AS builder

# Install build dependencies including g++ for chroma-hnswlib
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ python3-dev && \
    pip install --no-cache-dir --upgrade pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv (U2: pip install method, simplest in slim image — no curl needed)
RUN pip install --no-cache-dir uv

# Copy dependency manifests (uv.lock pins everything)
COPY code/python/pyproject.toml code/python/uv.lock ./

# Install the EXACT locked dependency set (F1: frozen lock, never an unlocked
# re-resolve of pyproject.toml). `uv export --frozen` reads uv.lock verbatim
# (fails loud if the lock is stale rather than silently drifting) and emits a
# fully-pinned requirements file; `uv pip install --system` installs it into the
# system site-packages so the runtime stage's
# `COPY --from=builder /usr/local/lib/python3.11/site-packages` keeps working unchanged.
# --no-dev = core deps only (prod default, no dev group).
#
# F8 = B HARD STEP: the preferred LLM provider's extra MUST be installed in prod.
# Current prod preferred_endpoint = openai (config/config_llm.yaml), and openai is a
# CORE/always-installed dependency — so NO `--extra` is needed today. If prod's
# preferred is ever switched to anthropic/gemini, add the matching `--extra <name>`
# to the `uv export` line below (e.g. `--extra gemini`), or the container will
# fail-hard at startup by design (fail-hard is the safety net; this line is the
# primary defense — see Task 4 Step 3b / F8).
RUN uv export --frozen --no-dev --no-hashes -o requirements-prod.txt && \
    uv pip install --system --no-cache -r requirements-prod.txt

# Stage 2: Runtime stage
FROM python:3.11-slim

# Apply security updates
RUN apt-get update && \
   apt-get install -y --no-install-recommends --only-upgrade \
       $(apt-get --just-print upgrade | grep "^Inst" | grep -i securi | awk '{print $2}') && \
   apt-get clean && \
   rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create a non-root user and set permissions
RUN groupadd -r nlweb && \
    useradd -r -g nlweb -d /app -s /bin/bash nlweb && \
    chown -R nlweb:nlweb /app

USER nlweb

# Bust cache for code layers on every deploy
ARG CACHE_BUST
# Copy application code with correct ownership
COPY --chown=nlweb:nlweb code/ /app/
COPY --chown=nlweb:nlweb static/ /app/static/
COPY --chown=nlweb:nlweb config/ /app/config/

# Copy installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Expose the port the app runs on
EXPOSE 8000

# Set environment variables
ENV NLWEB_OUTPUT_DIR=/app
ENV PYTHONPATH=/app
ENV PORT=8000
ENV NLWEB_CONFIG_DIR=/app/config
ENV NLWEB_STATIC_DIR=/app/static
ENV NLWEB_DATA_DIR=/data

# Command to run the application
CMD ["python", "python/app-aiohttp.py"]
