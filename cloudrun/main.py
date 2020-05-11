import os
import io
import json
import tempfile
import base64
import logging
import functools
from urllib.parse import urlparse

import numpy as np
import requests

from PIL import Image
from joblib import Memory
from flask import Flask, request

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.cloud import storage

PROFILE_URL = os.environ['PROFILE_URL']
GAMES_URL = os.environ['GAMES_URL']
MEDIA_URL = os.environ['MEDIA_URL']

# credentials.ApplicationDefault(), {'projectId': os.environ['GCP_PROJECT'],}
firebase_admin.initialize_app()

app = Flask(__name__)

db = firebase_admin.firestore.client()
memory = Memory(tempfile.gettempdir(), verbose=0)
session = requests.Session()
storage_client = storage.Client()
bucket = storage_client.get_bucket(os.environ['BUCKET'])


def get_steam_id(uid):
  logging.info(f'fetching steam_id of the user {uid}')
  try:
    return int(uid)
  except ValueError:
    pass
  response = session.get(PROFILE_URL.format(uid))
  response.raise_for_status()
  return response.json()['response']['steamid']


def get_games(sid):
  response = session.get(GAMES_URL.format(sid))
  response.raise_for_status()
  result = response.json()['response']['games']
  return sorted(result, key=lambda g: g['playtime_forever'], reverse=True)


@memory.cache
def download(url):
  try:
    image = Image.open(
      io.BytesIO(session.get(url).content))
    return np.asarray(image.convert('RGB'))
  except (OSError, IOError):
    logging.warning(f'failed to download or invalid image at {url}')


def generate(array, columns=10):
  length, height, width, intensity = array.shape
  rows = length // columns
  return Image.fromarray(
    array.reshape(rows, columns, height, width, intensity)
      .swapaxes(1, 2)
      .reshape(height * rows, width * columns, intensity))


@app.route('/', methods=['POST'])
def index():
  message = request.get_json()['message']

  data = json.loads(
    base64.b64decode(message['data']).decode('utf-8'))
  uid = data['uid']
  reference = db.collection('users').document(uid)

  try:
    sid = str(get_steam_id(uid))
    maximum = 8192
    games = [n for n in get_games(sid) if n['img_logo_url']][:maximum]
    build_url = lambda entry: MEDIA_URL.format(
      entry['appid'], entry['img_logo_url'])
    fetch = lambda game: functools.reduce(lambda g, f: f(g), [build_url, download], game)
    arr = np.array([g for g in map(fetch, games) if g is not None])
  except (KeyError, requests.exceptions.HTTPError):
    reference.set({'error': 'private profile or not found.'})
    return

  try:
    buffer = io.BytesIO()
    columns = 10
    nearest = lambda x, n: x - (x % n)
    limit = nearest(len(arr), columns)
    generate(arr[:limit], columns).save(buffer, 'JPEG', quality=90)
  except ValueError:
    reference.set({'error': 'internal error or insufficient amount of games to generate the image.'})
    return

  filepath = f'{sid[-1:]}/{sid}.jpg'
  blob = bucket.blob(filepath)
  blob.upload_from_string(buffer.getvalue(), content_type='image/jpeg')
  blob.make_public()
  reference.set({'url': 'https://gcs.steamosaic.com/%s' % (filepath)})

  return ('', 204)