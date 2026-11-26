ARG TAG=3.11.18-slim-trixie

FROM python:${TAG}

WORKDIR /connect

COPY requirements requirements

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# pip vendors its own CA bundle; refresh it after the October 2026 eMudhra intermediate CA key compromise.
RUN python -m pip install pip==26.2.2

RUN python -m pip install \
    -r requirements/format.txt \
    -r requirements/lint.txt \
    -r requirements/test.txt \
    -r requirements/synapse.txt

COPY . .

ENTRYPOINT ["python", "-m"]
