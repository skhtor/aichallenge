.PHONY: up down build logs restart ps db-shell invite-code

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose up --build -d

logs:
	docker-compose logs -f

restart:
	docker-compose restart web worker

ps:
	docker-compose ps

db-shell:
	docker-compose exec db psql -U ants -d ants

invite-code:
	@curl -s -X POST https://ants.sass.sh/api/admin/invite-codes -H "Authorization: $${ADMIN_SECRET}" -F "count=1"
