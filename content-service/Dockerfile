FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service

COPY content-service/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r /service/requirements.txt

COPY content-service /service/content-service
COPY shared_proto /service/shared_proto

ENV PYTHONPATH=/service

EXPOSE 50051

CMD ["python", "/service/content-service/app/server.py"]
