# Cairn team server.
#
# Verifies against src/cairn:
# - `cairn serve` flags: --host, --port, --team (src/cairn/cli.py, serve command)
# - defaults: host 127.0.0.1, port 4747, mode local (src/cairn/platform/config.py, ServerConfig)
# - local mode refuses a non-loopback bind, so team mode is not optional here
#   (config.py, ServerConfig.validate)
# - state lives under $CAIRN_HOME (platform.db, secret.key, server.toml, repos/, models/,
#   graph/) — mounted as a volume at /data
FROM python:3.12-slim

# git: the server clones projects registered by URL ($CAIRN_HOME/repos).
# ca-certificates: HTTPS clones and the model download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional extras at build time, e.g. --build-arg EXTRAS="[memory-server,temporal-neo4j]"
ARG EXTRAS=""
RUN pip install --no-cache-dir "cairn-brain${EXTRAS}"

# All state under one volume-mounted directory ($CAIRN_HOME, default ~/.cairn).
ENV CAIRN_HOME=/data \
    CAIRN_SERVER_MODE=team

RUN useradd --create-home --uid 1000 cairn \
    && mkdir -p /data \
    && chown cairn:cairn /data
USER cairn
VOLUME /data

EXPOSE 4747

# Entry: cairn serve --team --host 0.0.0.0 [--port 4747]
# (server.toml in $CAIRN_HOME overrides and extends these; see docs/configuration.md)
ENTRYPOINT ["cairn", "serve", "--team", "--host", "0.0.0.0"]
CMD ["--port", "4747"]
