FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements-app.txt
COPY --from=checks /requirements.txt ./requirements-tests.txt
RUN pip install --no-cache-dir -r requirements-app.txt -r requirements-tests.txt && useradd -u 1000 -m coach
COPY app ./app
COPY --from=checks /integration /checks/integration
COPY --from=checks /e2e /checks/e2e
USER coach
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/checks
