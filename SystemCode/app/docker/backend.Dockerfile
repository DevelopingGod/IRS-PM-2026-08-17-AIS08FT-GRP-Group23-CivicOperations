# The API and every component. Build from the repository root:
#   docker compose build
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /srv

# CPU only torch keeps the image about 2 GB smaller than the default CUDA build.
RUN pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
COPY app/backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# The sentence encoder the Intake bot uses, fetched once at build time so the container
# starts without internet access.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', revision='1110a243fdf4706b3f48f1d95db1a4f5529b4d41')"

COPY data/__init__.py data/__init__.py
COPY data/reference data/reference
COPY models models
COPY components components
COPY app/__init__.py app/__init__.py
COPY app/backend app/backend

RUN useradd --system --home /srv app && mkdir -p /srv/state && chown app /srv/state && chmod -R a+rX /opt/hf
USER app
ENV CIVICOPS_DB=/srv/state/civicops.db HF_HUB_OFFLINE=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/catalog', timeout=4)"
CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
