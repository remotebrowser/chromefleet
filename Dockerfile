FROM mirror.gcr.io/library/python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

RUN apt-get update -y && apt-get install --no-install-recommends -y curl podman gnupg

ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1

WORKDIR /app

COPY pyproject.toml uv.lock* ./
ENV VENV_PATH="/app/.venv"
ENV UV_FROZEN=1
RUN uv sync --no-dev --no-install-workspace

COPY chromefleet.py /app/chromefleet.py
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8300

RUN useradd -m -s /bin/bash chromefleet && \
    chown -R chromefleet:chromefleet /app && \
    usermod -aG sudo chromefleet && \
    echo 'chromefleet ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

USER chromefleet

ENTRYPOINT ["uv", "run", "chromefleet.py"]
