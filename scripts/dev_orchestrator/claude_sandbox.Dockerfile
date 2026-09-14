FROM node:22-bookworm-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.238 \
    && mkdir -p /home/node/.claude /workspace \
    && chown -R node:node /home/node/.claude /workspace
USER node
ENV HOME=/home/node
WORKDIR /workspace
ENTRYPOINT ["claude"]
