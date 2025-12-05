ARG TAG=3.9.6-slim-buster

FROM python:${TAG}

WORKDIR /connect

COPY requirements requirements

RUN python -m pip install \
    -r requirements/format.txt \
    -r requirements/lint.txt \
    -r requirements/test.txt \
    -r requirements/synapse.txt

COPY . .

ENTRYPOINT ["python", "-m"]
