.PHONY: deploy format lint update

.SILENT:

deploy:
	gcloud config set run/region us-central1
	gcloud beta run deploy pubsub --source . --project steamosaic

format:
	isort main.py
	black --line-length 120 main.py

lint:
	flake8 --max-line-length 120 main.py

update:
	pur -r requirements.txt
