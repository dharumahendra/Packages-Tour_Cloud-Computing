# Build dari root repo: docker build -f services/catalog/Dockerfile .
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY services/catalog/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/store.py shared/server.py ./
COPY services/catalog/app.py ./
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
