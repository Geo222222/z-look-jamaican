.PHONY: test run docker-up docker-down

test:
	pytest -q

run:
	uvicorn app.main:app --reload --port 8080

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
