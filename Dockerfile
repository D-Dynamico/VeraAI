FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vera/ ./vera/

RUN useradd --create-home --uid 1000 vera
USER vera

EXPOSE 8080

# One worker, always. The context store, conversation state and send ledger all
# live in memory, so a second worker would serve requests from a process that
# never saw the judge's context pushes.
CMD ["sh", "-c", "uvicorn vera.app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
