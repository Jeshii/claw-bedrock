FROM ghcr.io/jeshii/claw-bedrock:latest

COPY deploy/start_container.sh /app/start_container.sh
RUN chmod +x /app/start_container.sh
