FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install --no-deps face-recognition==1.3.0
COPY . .
ENV FLASK_ENV=production
EXPOSE 8000
# create/switch to non-root user here
CMD ["gunicorn", "-b", "0.0.0.0:8000", "wsgi:app"]