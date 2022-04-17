import base64
import functools
import http
import io
import json
import logging
import os
import tempfile

import numpy as np
from flask import Flask, request
from google.cloud.firestore import Client as FirestoreClient
from google.cloud.storage import Client as StorageClient
from joblib import Memory
from PIL import Image
from requests import Session
from requests.exceptions import HTTPError

app = Flask(__name__)

firestore = FirestoreClient()

memory = Memory(tempfile.gettempdir(), verbose=0)

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


@memory.cache
def download(url):
    try:
        image = Image.open(io.BytesIO(requests.get(url).content))

        # unfortunately, the images do not always have the same size, and
        # np.array requires the same size
        if image.width != CAPSULE_WIDTH or image.height != CAPSULE_HEIGHT:
            image = image.resize((CAPSULE_WIDTH, CAPSULE_HEIGHT), Image.ANTIALIAS)

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
        build_url = lambda e: MEDIA_URL.format(e["appid"])
        funcs = [build_url, download]
        fetch = lambda game: functools.reduce(lambda g, f: f(g), funcs, game)

        array = np.array([g for g in map(fetch, games) if g is not None])
    except (KeyError, HTTPError) as exc:
        logging.error(exc, exc_info=True)
        reference.set({"error": "private profile or not found."})
        return NO_CONTENT

    try:
        buffer = io.BytesIO()
        columns = 10
        nearest = lambda x, n: x - (x % n)
        limit = nearest(len(array), columns)

        generate(array[:limit], columns).save(buffer, "JPEG", quality=90)
    except (OSError, ValueError, io.UnsupportedOperation) as exc:
        logging.error(exc, exc_info=True)
        reference.set(
            {
                "error": "internal error or insufficient amount of games to generate the image."
            }
        )

        return NO_CONTENT

    filepath = f"{sid[-1:]}/{sid}.jpg"

    blob = bucket.blob(filepath)
    blob.upload_from_string(buffer.getvalue(), content_type="image/jpeg")
    blob.make_public()

    reference.set({"url": "https://gcs.steamosaic.com/%s" % (filepath)})

    return NO_CONTENT
