# Multi-arch (works on x86 PCs and ARM boards like Raspberry Pi / DietPi)
FROM python:3.12-slim

WORKDIR /app

# Optional: LibreOffice for legacy .doc/.ppt and OpenDocument/RTF files.
# Adds several hundred MB, so it's off by default:
#   docker compose build --build-arg WITH_LIBREOFFICE=1
ARG WITH_LIBREOFFICE=0
RUN if [ "$WITH_LIBREOFFICE" = "1" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            libreoffice-writer libreoffice-impress libreoffice-calc && \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
