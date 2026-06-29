# ResQNet Simulator — Dockerfile
# Place at: simulator/Dockerfile

FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY simulator.py .
COPY send_updates.py .

# stdin_open + tty must be set in docker-compose for keypress mode
CMD ["python", "simulator.py", "--url", "http://backend:8000/device/update"]