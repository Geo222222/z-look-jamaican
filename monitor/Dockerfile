FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY monitor/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --system --uid 10001 --create-home monitor
COPY monitor /app/monitor
USER monitor
EXPOSE 8080
CMD ["uvicorn", "monitor.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
