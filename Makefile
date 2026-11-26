.PHONY: demo up seed app stop clean test

# The full walkthrough: stack, seed, shop, traffic, audit, gate.
demo:
	python3 demo/demo.py

# Just self-hosted Flagsmith on :8000.
up:
	docker compose up -d

# Create the admin and the experiments, then start the shop on :8080.
seed:
	python3 demo/seed.py

app:
	docker compose --profile app up -d --build demo

stop:
	docker compose --profile app down

clean:
	docker compose --profile app down -v

test:
	python3 -m pytest tests -q
