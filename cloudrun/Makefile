.PHONY: deploy format update

.SILENT:

deploy:
	gcloud beta run deploy pubsub --source . --project steamosaic

format:
	isort main.py
	black main.py

update:
	pur -r requirements.txt