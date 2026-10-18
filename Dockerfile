FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app
COPY . /app
RUN uv pip install --system -r requirements-service.txt
ARG INSTALL_PROFILES=analytics,openbb,vibe,tradingagents,nautilus
RUN python scripts/bootstrap_repos.py --help
RUN python scripts/bootstrap_envs.py --profiles "$INSTALL_PROFILES" --destination /opt/prop-envs
RUN python scripts/bootstrap_repos.py --destination /app/vendor
ENV PROP_ENVS_DIR=/opt/prop-envs PROP_BIND=0.0.0.0
CMD ["python", "scripts/start_services.py"]
