FROM python:3.11-slim

# tkinter is a system package required transitively by ui.utils and utils.encryption.
# ffmpeg is optional but enables broader audio format support via soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-tk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying app code so layer is cached.
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Copy application source.
COPY . .

# /data/working  — templates, guidelines, reports (bind-mount or named volume)
# /root/.voxrad  — encrypted API keys + settings.ini (named volume)
VOLUME ["/data/working", "/root/.voxrad"]

ENV VOXRAD_WEB_PASSWORD=voxrad \
    VOXRAD_WORKING_DIR=/data/working

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python - <<'EOF'
import urllib.request, sys
try:
    urllib.request.urlopen("http://localhost:8765/", timeout=4)
    sys.exit(0)
except Exception:
    sys.exit(1)
EOF

CMD ["python", "VoxRad.py", "--web", "--host", "0.0.0.0", "--port", "8765"]
