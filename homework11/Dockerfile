FROM python:3.12.8-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY homework11/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY api_service.py simulated_backend.py release.py /app/
USER 10001
EXPOSE 8092
CMD ["python", "api_service.py", "--backend", "simulated", "--host", "0.0.0.0", "--simulated-config", "/config/simulator.json"]
