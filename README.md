# NoteTube

An AI-powered web app that turns your favorite YouTube video tutorials into organized and structured notes

---

## Tech Stack

- Python
- Django
- PostgreSQL
- Docker
- Tailwind CSS

---

## Features (updating...)

- **End-to-end app flow**
  - Frontend + backend integrated into a **fullstack app**
  - Authenticated users can:
    - Sign up / log in
    - Create notes with AI

---

## 📚 What I Learned From This Project

- **Working with Django**  
  I learned how to work with the Django framework and its quick-to-develop qualities, like a built-in authentication system.

- **Tailwind CSS**  
  I worked on adding some styling using Tailwind CSS instead of just using normal CSS.

---

## Running the Project

### To run the project locally, follow these steps:

  1. Clone the repo (git clone <url>)
  2. Create a virtual environment (python3 -m venv venv)
  3. Activate the environment (source venv/bin/activate)
  4. Install requirements (pip install -r requirements.txt)
  5. Run locally (python manage.py runserver)
     
### Run with Docker

  2. Build image (docker build -t notetube:latest)
  3. Run image (docker run --rm -p 8000:8000 --env-file .env notetube:latest)


