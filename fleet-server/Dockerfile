FROM python:3.12-slim
WORKDIR /app
COPY fleet-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fleet-server/ .
COPY scripts/install.sh /app/scripts/install.sh
COPY fleet-agent/agent.py /app/agent/agent.py
EXPOSE 8765
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
