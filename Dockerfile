FROM python:3.12-slim
ARG SERVICE
ENV SERVICE=${SERVICE} PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gamehub gamehub
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn gamehub.web:app --host 0.0.0.0 --port ${PORT:-8080}"]

