FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# `Backend/` already contains `templates/` (copied above), so no separate templates copy is needed

EXPOSE 8000

# Run the Django development server by default (you can override at runtime)
CMD ["python", "Backend/manage.py", "runserver", "0.0.0.0:8000"]

# Build: docker build -t <proj-name>:latest .
# Run:  docker run --rm -p 8000:8000 --env-file .env <proj-name>:latest