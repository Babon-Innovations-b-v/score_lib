FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git libsndfile1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.20
WORKDIR /score_lib
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY config ./config
COPY schemas ./schemas
COPY worlds ./worlds
COPY definitions ./definitions
COPY entries ./entries
COPY index ./index
ENV PATH="/score_lib/.venv/bin:${PATH}"
ENTRYPOINT ["score-lib"]
