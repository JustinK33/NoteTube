FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# installed ffmpeg for ec2 instance
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh","-c","python Backend/manage.py collectstatic --noinput && gunicorn --chdir Backend notetube.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile - --error-logfile - --log-level debug"]

