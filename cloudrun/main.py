import base64
import functools
import http
import io
import json
import logging
import os
from datetime import datetime

import numpy as np
from flask import Flask, request
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.storage import Client as StorageClient
from PIL import Image
from requests import Session
from requests.exceptions import HTTPError

app = Flask(__name__)

firestore = FirestoreClient()

requests = Session()

storage = StorageClient()

bucket = storage.get_bucket(os.environ["BUCKET"])

PROFILE_URL = os.environ["PROFILE_URL"]
GAMES_URL = os.environ["GAMES_URL"]
MEDIA_URL = os.environ["MEDIA_URL"]

NO_CONTENT = "", http.HTTPStatus.NO_CONTENT
CAPSULE_WIDTH = 184
CAPSULE_HEIGHT = 69


def get_steam_id(uid):
    logging.info(f"fetching steam_id of the user {uid}")

    try:
        return int(uid)
    except ValueError:
        pass

    response = requests.get(PROFILE_URL.format(uid))
    response.raise_for_status()
    return response.json()["response"]["steamid"]


def get_games(sid):
    response = requests.get(GAMES_URL.format(sid))
    response.raise_for_status()
    result = response.json()["response"]["games"]
    return sorted(result, key=lambda g: g["playtime_forever"], reverse=True)


def build_url(e):
    return MEDIA_URL.format(e["appid"], int(datetime.now().timestamp()))


def nearest(q, n):
    return q - (q % n)


def download(url):
    try:
        image = Image.open(io.BytesIO(requests.get(url).content))

        # unfortunately, the images do not always have the same size, and
        # np.array requires the same size
        if image.width != CAPSULE_WIDTH or image.height != CAPSULE_HEIGHT:
            image = image.resize((CAPSULE_WIDTH, CAPSULE_HEIGHT), Image.LANCZOS)

        return np.asarray(image.convert("RGB"))
    except (OSError, IOError):
        logging.warning(f"failed to download or invalid image at {url}")


def generate(array, columns):
    length, height, width, intensity = array.shape

    rows = length // columns

    return Image.fromarray(
        array.reshape(rows, columns, height, width, intensity)
        .swapaxes(1, 2)
        .reshape(height * rows, width * columns, intensity)
    )


@app.route("/", methods=["POST"])
def index():
    message = request.get_json()["message"]
    data = json.loads(base64.b64decode(message["data"]).decode("utf-8"))
    uid = data["uid"]
    reference = firestore.collection("users").document(uid)

    try:
        sid = str(get_steam_id(uid))
        games = get_games(sid)
        array = np.array(
            [
                game
                for game in map(lambda game: functools.reduce(lambda g, f: f(g), [build_url, download], game), games)
                if game is not None
            ]
        )
    except (KeyError, HTTPError) as exc:
        logging.error(exc, exc_info=True)
        reference.update({"error": "private profile or not found."})
        return NO_CONTENT

    try:
        buffer = io.BytesIO()
        columns = 10
        limit = nearest(len(array), columns)

        generate(array[:limit], columns).save(buffer, "JPEG", quality=90)
    except Exception as exc:  # noqa
        logging.error(exc, exc_info=True)
        reference.update({"error": "internal error or insufficient amount of games to generate the image."})
        return NO_CONTENT

    filepath = f"{sid[-1:]}/{sid}.jpg"

    blob = bucket.blob(filepath)
    blob.upload_from_string(buffer.getvalue(), content_type="image/jpeg")
    blob.make_public()

    reference.update({"url": "https://gcs.steamosaic.com/%s" % (filepath)})

    return NO_CONTENT
