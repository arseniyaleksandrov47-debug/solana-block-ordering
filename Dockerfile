FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

COPY . .

# Default command runs full non-destructive analysis and verification
ENTRYPOINT ["python", "-m", "solana_scheduler.cli"]
CMD ["analyze", "--sample-size", "10", "--output-dir", "data"]
