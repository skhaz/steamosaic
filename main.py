import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime
from datetime import timedelta
from typing import List
from typing import Optional

import aiohttp
import cv2
import numpy as np
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Response

STEAM_PROFILE_URL = os.environ["STEAM_PROFILE_URL"]
STEAM_GAMES_URL = os.environ["STEAM_GAMES_URL"]
STEAM_MEDIA_URL = os.environ["STEAM_MEDIA_URL"]

app = FastAPI()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def fetch(url: str, semaphore: asyncio.Semaphore, session: aiohttp.ClientSession) -> Optional[np.ndarray]:
    async with semaphore:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.read()
                buffer = np.frombuffer(data, np.uint8)
                image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
                return image
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                return None
            logger.exception("Error fetching URL %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.exception("Error fetching URL %s: %s", url, exc)
            return None


async def download(urls: List[str]) -> List[np.ndarray]:
    worker_count = 4 * (os.cpu_count() or 1)
    semaphore = asyncio.Semaphore(worker_count)
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit_per_host=worker_count)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [fetch(url, semaphore, session) for url in urls]
        images = await asyncio.gather(*tasks)
    return [img for img in images if img is not None]


def create_mosaic(images: List[np.ndarray], columns: int = 10) -> Optional[np.ndarray]:
    if not images:
        return None
    rows = []
    row_widths = []
    for i in range(0, len(images), columns):
        row_imgs = images[i : i + columns]
        min_width = min(img.shape[1] for img in row_imgs)
        min_height = min(img.shape[0] for img in row_imgs)
        cropped = []
        for img in row_imgs:
            h, w = img.shape[:2]
            start_x = (w - min_width) // 2
            start_y = (h - min_height) // 2
            cropped.append(img[start_y : start_y + min_height, start_x : start_x + min_width])
        if len(cropped) < columns:
            pad_img = np.zeros((min_height, min_width, 3), dtype=np.uint8)
            cropped.extend([pad_img] * (columns - len(cropped)))
        row = np.hstack(cropped)
        rows.append(row)
        row_widths.append(row.shape[1])
    global_width = min(row_widths)
    final_rows = []
    for row in rows:
        if row.shape[1] > global_width:
            crop_left = (row.shape[1] - global_width) // 2
            row = row[:, crop_left : crop_left + global_width]
        final_rows.append(row)
    return np.vstack(final_rows)


async def get_cover_urls(username: str) -> List[str]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(STEAM_PROFILE_URL.format(username)) as response:
            response.raise_for_status()
            profile_data = await response.json()
        if profile_data.get("response", {}).get("success") != 1:
            return []
        steam_id = profile_data["response"].get("steamid")
        async with session.get(STEAM_GAMES_URL.format(steam_id)) as response:
            response.raise_for_status()
            games_data = await response.json()
    games = games_data.get("response", {}).get("games", [])
    games.sort(key=lambda game: game.get("playtime_forever", 0), reverse=True)
    timestamp = int(time.time())
    return [STEAM_MEDIA_URL.format(game["appid"], timestamp) for game in games if "appid" in game]


@app.get("/{username}.jpeg")
async def mosaic(username: str):
    urls = await get_cover_urls(username)
    if not urls:
        logger.error("Unable to retrieve cover URLs for user %s", username)
        raise HTTPException(status_code=404, detail="Cover URLs not found.")
    images = await download(urls)
    if not images:
        logger.error("Failed to download images for user %s", username)
        raise HTTPException(status_code=404, detail="Images download failed.")
    mosaic = create_mosaic(images, columns=10)
    if mosaic is None:
        logger.error("Failed to create mosaic for user %s", username)
        raise HTTPException(status_code=500, detail="Mosaic creation failed.")
    success, encoded = cv2.imencode(".jpg", mosaic)
    if not success:
        logger.error("Failed to encode mosaic for user %s", username)
        raise HTTPException(status_code=500, detail="Image encoding failed.")

    etag = hashlib.sha256("".join(urls).encode("utf-8")).hexdigest()
    duration = timedelta(weeks=4)
    expires = (datetime.utcnow() + duration).strftime("%a, %d %b %Y %H:%M:%S GMT")
    headers = {
        "ETag": etag,
        "Cache-Control": f"public, max-age={int(duration.total_seconds())}, immutable",
        "Expires": expires,
    }
    return Response(content=encoded.tobytes(), media_type="image/jpeg", headers=headers)


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=3000)


if __name__ == "__main__":
    main()
