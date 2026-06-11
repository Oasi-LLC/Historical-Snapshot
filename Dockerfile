FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY config ./config
COPY src ./src
COPY api ./api
COPY dashboard ./dashboard
COPY data ./data

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

ENV PYTHONPATH=/app/src

EXPOSE 8000 8502
