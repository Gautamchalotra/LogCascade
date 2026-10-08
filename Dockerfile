FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
# CPU-only torch keeps the image small; swap the index for GPU builds.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && chown -R app /app
USER app
ENV LOGCASCADE_CONFIG=configs/default.yaml LOGCASCADE_DATASET=HDFS
EXPOSE 8000
# Mount trained artifacts:  -v $(pwd)/data:/app/data
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
