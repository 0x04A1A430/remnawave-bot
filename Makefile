.PHONY: up down reload test

up:
	docker compose up -d

down:
	docker compose down

reload: down up

test:
	pytest
