.PHONY: up up-follow down reload reload-follow test

up:
	docker compose up -d --build

up-follow:
	docker compose up --build

down:
	docker compose down

reload: down up

reload-follow: down up-follow

test:
	pytest
