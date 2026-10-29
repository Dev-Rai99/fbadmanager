FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ (snapshots, saved ad images, lead records) and output/ are written at
# runtime: mount a volume on /app/data so they survive a redeploy.
RUN useradd --create-home app && mkdir -p /app/data /app/output && chown -R app:app /app
USER app

# Secrets are NOT in the image. Pass FB_ACCESS_TOKEN, FB_AD_ACCOUNT_ID,
# FB_PAGE_ID (and optionally FB_PIXEL_ID, EKO_ADMIN_*) as environment
# variables. The dashboard has no login: keep this on an internal network.
ENV PORT=5000
EXPOSE 5000

# One worker on purpose: the dashboard cache and its background refresh live in
# this process's memory. Threads handle concurrent requests.
CMD ["sh", "-c", "exec gunicorn --chdir webapp --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:${PORT} app:app"]
