# Stage 1: Build stage
FROM python:3.11-slim AS builder

# Install build dependencies including g++ for chroma-hnswlib
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ python3-dev && \
    pip install --no-cache-dir --upgrade pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements file
COPY code/python/requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

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
