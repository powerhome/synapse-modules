ARG TAG=3.10.20-slim-trixie

FROM python:${TAG}

WORKDIR /connect

COPY requirements requirements

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install \
    -r requirements/format.txt \
    -r requirements/lint.txt \
    -r requirements/test.txt \
    -r requirements/synapse.txt

COPY . .

ENTRYPOINT ["python", "-m"]
