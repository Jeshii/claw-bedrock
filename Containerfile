FROM python:3.12-slim

WORKDIR /app

# Install AWS CLI
RUN pip install --no-cache-dir awscli

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY config.bedrock.yaml .
COPY token_refresher.py .
COPY management_app.py .
COPY start_container.sh .

# Make start script executable
RUN chmod +x start_container.sh

# Expose ports (LiteLLM + Management UI)
EXPOSE 4000 8080 8282

ENTRYPOINT ["./start_container.sh"]
