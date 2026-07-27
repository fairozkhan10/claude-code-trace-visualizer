# Agent + fixture image for network-isolated SWE-bench runs.
#
# PY defaults to 3.9 because SWE-bench targets period-correct interpreters:
# sympy 1.5's test shim and pytest 7.x need 3.9 (pytest 7.2 collapses on 3.12,
# where ast.Str was removed). Override with --build-arg PY=3.11 etc.
ARG PY=3.9
FROM python:${PY}-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# claude refuses --dangerously-skip-permissions as root
RUN useradd -m -s /bin/bash agent
USER agent
ENV HOME=/home/agent
WORKDIR /home/agent
