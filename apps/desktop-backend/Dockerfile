FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 rsap
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=rsap:rsap . .
USER rsap
EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"]
