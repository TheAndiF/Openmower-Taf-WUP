FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY controller.py /app/controller.py
COPY bot_commands.example.xml /app/bot_commands.example.xml

CMD ["python", "/app/controller.py"]
