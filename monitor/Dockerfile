FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /monitor
COPY monitor/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt
COPY monitor/app ./app
COPY monitor/web ./web
RUN useradd -r -u 10001 monitor && chown -R monitor:monitor /monitor
USER monitor
EXPOSE 3000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","3000"]
