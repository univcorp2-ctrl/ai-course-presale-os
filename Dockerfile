FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir .
COPY config /app/config
COPY content /app/content
COPY docs /app/docs
RUN mkdir -p /app/var /app/artifacts

EXPOSE 8000
CMD ["uvicorn", "courseforge.web:app", "--host", "0.0.0.0", "--port", "8000"]
