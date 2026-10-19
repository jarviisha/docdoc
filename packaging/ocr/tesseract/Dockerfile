# A Tesseract server speaking the PaddleX `/ocr` serving protocol -- see
# `server/app.py`'s own docstring for what it replaces and why.
#
# Build from this directory:
#   docker build -f packaging/ocr/tesseract/Dockerfile -t tesseract-ocr packaging/ocr/tesseract

FROM python:3.11-slim-bookworm

# `tesseract-ocr` is the engine; `-vie` and `-eng` are the two languages the
# shim's host ever served (`TESSERACT_LANGS` defaults to `vie+eng` in
# `server/app.py`) -- a deployment wanting a third language adds its own
# `tesseract-ocr-<code>` package and sets the environment variable to match.
#
# `--no-install-recommends` and the apt list cleanup keep the image from
# carrying a doc viewer and a cache neither this server nor its healthcheck
# ever opens.
RUN apt-get update && apt-get install --no-install-recommends -y \
      tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./

# Not root. There is nothing in this container worth privilege, and running
# as root buys nothing a non-root user doesn't already get (same reasoning
# as packaging/docker/Dockerfile's own `USER docdoc`).
RUN useradd --create-home --uid 10001 tesseract
USER tesseract

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
