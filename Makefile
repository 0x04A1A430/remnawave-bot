# RemnaWave Bot Docker Management

.PHONY: help build up down restart logs clean db-backup db-restore

# Default target
help:
	@echo "Available commands:"
	@echo "  build     - Build all Docker images"
	@echo "  up        - Start all services"
	@echo "  up-min    - Start only bot and database (minimal setup)"
	@echo "  up-full   - Start all services including nginx and redis"
	@echo "  down      - Stop all services"
	@echo "  restart   - Restart all services"
	@echo "  logs      - Show logs for all services"
	@echo "  logs-bot  - Show logs for bot service only"
	@echo "  logs-db   - Show logs for database service only"
	@echo "  clean     - Remove all containers, networks, and volumes"
	@echo "  db-backup - Create database backup"
	@echo "  db-restore - Restore database from backup"
	@echo "  shell-bot - Open shell in bot container"
	@echo "  shell-db  - Open shell in database container"

# Build all images
build:
	docker compose build

# Start minimal services (bot + database)
up-min: setup-dirs
	docker compose up -d postgres bot

# Start all services including optional ones
up-full: setup-dirs
	docker compose --profile with-nginx up -d

# Start main services (default)
up: setup-dirs
	docker compose up -d postgres redis bot

# Setup required directories
setup-dirs:
	mkdir -p logs data backups

# Stop all services
down:
	docker compose down

# Restart all services
restart:
	docker compose restart

# Show logs for all services
logs:
	docker compose logs -f

# Show logs for bot only
logs-bot:
	docker compose logs -f bot

# Show logs for database only  
logs-db:
	docker compose logs -f postgres

# Clean up everything (DANGEROUS - removes all data)
clean:
	@echo "This will remove all containers, networks, and volumes. Are you sure? [y/N]"
	@read answer && [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]
	docker compose down -v --remove-orphans
	docker system prune -f

# Database backup
db-backup:
	@mkdir -p backups
	docker compose exec postgres pg_dump -U remnawave_user remnawave_bot > backups/backup_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "Backup created in backups/ directory"

# Database restore (use: make db-restore BACKUP=backup_20231201_120000.sql)
db-restore:
	@if [ -z "$(BACKUP)" ]; then echo "Usage: make db-restore BACKUP=backup_file.sql"; exit 1; fi
	docker compose exec -T postgres psql -U remnawave_user -d remnawave_bot < backups/$(BACKUP)
	@echo "Database restored from $(BACKUP)"

# Open shell in bot container
shell-bot:
	docker compose exec bot /bin/bash

# Open shell in database container
shell-db:
	docker compose exec postgres psql -U remnawave_user -d remnawave_bot

# Check services status
status:
	docker compose ps

# View service resource usage
stats:
	docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
