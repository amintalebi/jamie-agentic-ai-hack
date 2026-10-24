# Call worker (role A). Build from the repo root so common/ is included:
#   docker build -f call_agent/Dockerfile -t jamie-call-agent .
#   docker run --env-file .env jamie-call-agent
# Needs the same .env as the server: LIVEKIT_*, LIVEKIT_SIP_OUTBOUND_TRUNK_ID, ELEVENLABS_*, ANTHROPIC_API_KEY,
# RESON8_API_KEY (optional), SERVER_PUBLIC_URL, CALL_AGENT_NAME.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY call_agent/requirements.txt call_agent/requirements.txt
# livekit-plugins-noise-cancellation is imported by agent.py (optional at runtime, see the try/except);
# listed here rather than in E's requirements.txt. livekit-api is pulled in by livekit-agents.
RUN pip install --no-cache-dir -r call_agent/requirements.txt livekit-plugins-noise-cancellation

COPY common/ common/
COPY call_agent/ call_agent/
# fixtures/ is only for console mode and is excluded by the root .dockerignore; not copied.

# Plugin model files (ElevenLabs) so the first call does not download them.
RUN python call_agent/agent.py download-files

# Production mode: no dev reload, connects to LIVEKIT_URL and waits for dispatches.
CMD ["python", "call_agent/agent.py", "start"]
