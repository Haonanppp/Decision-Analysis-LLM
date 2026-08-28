# Decision Analysis web UI. The claude-agent-sdk drives the Claude Code CLI,
# which needs a Node.js runtime inside the image.
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY decision_analysis ./decision_analysis
COPY skills ./skills

ENV PYTHONUNBUFFERED=1
# Case output lives on the mounted volume when DA_CASES_DIR is set
# (Railway: attach a volume at /data and set DA_CASES_DIR=/data/cases).
CMD ["python", "-m", "decision_analysis.webserver"]
