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

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.cloud import storage

PROFILE_URL = os.environ['PROFILE_URL']
GAMES_URL = os.environ['GAMES_URL']
MEDIA_URL = os.environ['MEDIA_URL']

firebase_admin.initialize_app(
  credentials.ApplicationDefault(), {
    'projectId': os.environ['GCP_PROJECT'],
  })

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


def get_games(steam_id):
  response = session.get(GAMES_URL.format(steam_id))
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


def pubsub(event, context):
  message = json.loads(
    base64.b64decode(event['data']).decode('utf-8'))
  uid = message['uid']
  docRef = db.collection('users').document(uid)

  try:
    steam_id = str(get_steam_id(uid))
    maximum = 10000
    games = [n for n in get_games(steam_id) if n['img_logo_url']][:maximum]
    build_url = lambda entry: MEDIA_URL.format(
      entry['appid'], entry['img_logo_url'])
    fetch = lambda game: functools.reduce(lambda g, f: f(g), [build_url, download], game)
    arr = np.array([g for g in map(fetch, games) if g is not None])
  except (KeyError, requests.exceptions.HTTPError):
    docRef.set({'error': "private profile or not found."})
    return

  buffer = io.BytesIO()
  columns = 10
  nearest = lambda x, n: x - (x % n)
  limit = nearest(len(arr), columns)
  try:
    generate(arr[:limit], columns).save(buffer, 'JPEG', quality=90)
  except ValueError:
    docRef.set({'error': "internal error or insufficient amount of games to generate the image."})
    return

  filename = os.path.join(steam_id[-1:], ''.join([steam_id, '.jpg']))
  blob = bucket.blob(filename)
  blob.upload_from_string(buffer.getvalue(), content_type='image/jpeg')
  blob.make_public()
  url = 'https://%s/%s' % (os.environ['BUCKET'], filename)
  docRef.set({'url': url})
