FROM python:3.11-slim

WORKDIR /app

# System deps: libpq for psycopg, build-essential for any C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CRLF-safe + executable. The repo is edited on Windows, so git may hand this
# script CRLF line endings, which /bin/sh would parse as a syntax error. The
# in-place sed normalises them inside the image.
RUN sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
    && chmod +x /app/scripts/docker-entrypoint.sh

EXPOSE 8000

# No --reload. V1 runs ingestion in-process (project.md Section 2: no worker
# queue), so an auto-reload triggered by a code edit cancels the in-flight
# ingestion task and leaves the document stuck mid-pipeline. Same reasoning
# behind RELOAD=false being mandatory in backend/.env.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
