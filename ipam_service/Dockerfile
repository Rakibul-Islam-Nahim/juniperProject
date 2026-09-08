FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg — none required for the slim variant.

COPY . ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]