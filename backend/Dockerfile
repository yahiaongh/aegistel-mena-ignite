FROM python:3.12-slim

# Prevent Python from writing pyc files & configure HF Space Port
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

# Install system dependencies (sqlite needed by crewai/mem0 occasionally)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces strictly require non-root user '1000'
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Copy dependencies list and install securely as user
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=user:user . .

# Expose HF Port
EXPOSE 7860

# Launch Uvicorn server bound to the HF environment port
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]