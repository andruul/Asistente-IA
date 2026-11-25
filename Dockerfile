FROM python:3.10-slim

WORKDIR /app

# Evita que Python escriba archivos .pyc y fuerza salida sin búfer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py bot.py database.py tools.py ./

CMD ["python", "bot.py"]
