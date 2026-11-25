# Oink engine: Docling (document reading) + BGE-M3 (embeddings), CPU only.
# Models are downloaded on first use into /models (a volume), not baked into the image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf EASYOCR_MODULE_PATH=/models/easyocr OMP_NUM_THREADS=3 \
    TOKENIZERS_PARALLELISM=false MALLOC_TRIM_THRESHOLD_=65536
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.5,<3" "torchvision>=0.20,<1"
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY app.py /app/app.py
RUN useradd -u 1000 -m engine && mkdir -p /models && chown engine /models
USER engine
WORKDIR /app
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8090/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
