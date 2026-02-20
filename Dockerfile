FROM python:3.12-slim

# Install ffmpeg and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (needed by yt-dlp for YouTube extraction)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_DIR="/root/.deno"
ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp
RUN pip install --no-cache-dir yt-dlp

# Copy app
COPY app.py .
COPY templates/ templates/

EXPOSE 8888

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8888"]
