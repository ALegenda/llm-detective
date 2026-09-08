FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home detective && mkdir /data && chown detective:detective /data
COPY app app
COPY static static
COPY scripts scripts
USER detective
VOLUME /data
EXPOSE 8080
# Render injects PORT (default 10000); local/compose keep 8080.
CMD ["/bin/sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
