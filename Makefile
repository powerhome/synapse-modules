export APP_IMAGE_REPO= # ignored
export APP_IMAGE_TAG=  # ignored

COMPOSE=docker compose -f ../docker-compose.ci.yml
COMPOSE_BUILD=${COMPOSE} build -q builder
COMPOSE_RUN=${COMPOSE} run --rm -e PYTHONPATH=/ builder
COMPOSE_SHELL=${COMPOSE} run --rm --entrypoint /bin/bash builder

all: format lint test

image:
	$(COMPOSE_BUILD)

format: image
	$(COMPOSE_RUN) black . -v
	$(COMPOSE_RUN) isort .

lint: image
	$(COMPOSE_RUN) black . --check -v
	$(COMPOSE_RUN) flake8

test: image
	$(COMPOSE_RUN) unittest discover -v -s /connect/tests -t .

shell: image
	$(COMPOSE_SHELL)
