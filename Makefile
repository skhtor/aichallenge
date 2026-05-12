.PHONY: up down build logs restart ps db-shell

up:
	docker-compose -f docker-compose.dev.yml up -d

down:
	docker-compose -f docker-compose.dev.yml down

build:
	docker-compose -f docker-compose.dev.yml up --build -d

logs:
	docker-compose -f docker-compose.dev.yml logs -f

restart:
	docker-compose -f docker-compose.dev.yml restart web worker

ps:
	docker-compose -f docker-compose.dev.yml ps

db-shell:
	docker-compose -f docker-compose.dev.yml exec db psql -U ants -d ants
