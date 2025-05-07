import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import cast

import aiohttp
import cv2
import numpy as np
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Response
from fastapi.responses import FileResponse
from mangum import Mangum
from numpy import uint8
from numpy.typing import NDArray

ImageArray = NDArray[uint8]

STEAM_PROFILE_URL: str = os.environ["STEAM_PROFILE_URL"]
STEAM_GAMES_URL: str = os.environ["STEAM_GAMES_URL"]
STEAM_MEDIA_URL: str = os.environ["STEAM_MEDIA_URL"]

app: FastAPI = FastAPI()

logger: logging.Logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def fetch(
    url: str,
    semaphore: asyncio.Semaphore,
    session: aiohttp.ClientSession,
) -> Optional[ImageArray]:
    async with semaphore:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                data: bytes = await response.read()
                buffer: np.ndarray = np.frombuffer(data, np.uint8)
                image: Optional[ImageArray] = cast(Optional[ImageArray], cv2.imdecode(buffer, cv2.IMREAD_COLOR))
                return image
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                return None
            logger.exception("Error fetching URL %s: %s", url)
            return None

        except:  # noqa
            logger.exception("Error fetching URL %s: %s", url)
            return None


async def download(urls: List[str]) -> List[ImageArray]:
    worker_count: int = 4 * (os.cpu_count() or 1)
    semaphore: asyncio.Semaphore = asyncio.Semaphore(worker_count)
    timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=30)
    connector: aiohttp.TCPConnector = aiohttp.TCPConnector(limit_per_host=worker_count)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks: List[asyncio.Task[Optional[ImageArray]]] = [
            asyncio.create_task(fetch(url, semaphore, session)) for url in urls
        ]
        results: List[Optional[ImageArray]] = await asyncio.gather(*tasks)

    return [img for img in results if img is not None]


def create_mosaic(images: List[ImageArray], columns: int = 10) -> Optional[ImageArray]:
    if not images:
        return None

    mosaic_rows: List[np.ndarray] = []
    for i in range(0, len(images), columns):
        row_imgs: List[ImageArray] = images[i : i + columns]
        min_width: int = min(img.shape[1] for img in row_imgs)
        min_height: int = min(img.shape[0] for img in row_imgs)
        cropped_row: List[ImageArray] = [
            img[
                (img.shape[0] - min_height) // 2 : (img.shape[0] - min_height) // 2 + min_height,
                (img.shape[1] - min_width) // 2 : (img.shape[1] - min_width) // 2 + min_width,
            ]
            for img in row_imgs
        ]

        if len(cropped_row) < columns:
            pad_img: ImageArray = np.zeros((min_height, min_width, 3), dtype=np.uint8)
            cropped_row.extend([pad_img] * (columns - len(cropped_row)))

        mosaic_rows.append(np.hstack(cropped_row))
    target_width: int = min(row.shape[1] for row in mosaic_rows)
    cropped_rows: List[np.ndarray] = [
        row[
            :,
            (row.shape[1] - target_width) // 2 : (row.shape[1] - target_width) // 2 + target_width,
        ]
        for row in mosaic_rows
    ]

    return np.vstack(cropped_rows)


async def get_cover_urls(username: str) -> List[str]:
    timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(STEAM_PROFILE_URL.format(username)) as response:
            response.raise_for_status()
            profile_data: Dict[str, Any] = await response.json()

        if profile_data.get("response", {}).get("success") != 1:
            return []

        steam_id: str = profile_data["response"].get("steamid", "")
        async with session.get(STEAM_GAMES_URL.format(steam_id)) as response:
            response.raise_for_status()
            games_data: Dict[str, Any] = await response.json()

    games: List[Dict[str, Any]] = games_data.get("response", {}).get("games", [])
    games.sort(key=lambda game: game.get("playtime_forever", 0), reverse=True)
    timestamp: int = int(time.time())

    return [STEAM_MEDIA_URL.format(game["appid"], timestamp) for game in games if "appid" in game]


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "index.html"),
        media_type="text/html",
    )


@app.get("/favicon.ico")
async def favicon():
    duration = timedelta(days=365)
    headers = {"Cache-Control": f"public, max-age={int(duration.total_seconds())}, immutable"}
    return Response(content=b"", media_type="image/x-icon", headers=headers)


@app.get("/{username}.jpeg")
async def mosaic(username: str) -> Response:
    urls: List[str] = await get_cover_urls(username)
    if not urls:
        logger.error("Failed to retrieve cover URLs for user %s", username)
        raise HTTPException(status_code=404, detail="Cover URLs not found.")

    images: List[ImageArray] = await download(urls)
    if not images:
        logger.error("Failed to download images for user %s", username)
        raise HTTPException(status_code=404, detail="Image download failed.")

    mosaic_image: Optional[ImageArray] = create_mosaic(images, columns=10)
    if mosaic_image is None:
        logger.error("Failed to create mosaic for user %s", username)
        raise HTTPException(status_code=500, detail="Mosaic creation failed.")

    success: bool
    encoded: np.ndarray
    success, encoded = cv2.imencode(".jpg", mosaic_image)
    if not success:
        logger.error("Failed to encode mosaic for user %s", username)
        raise HTTPException(status_code=500, detail="Image encoding failed.")

    etag: str = hashlib.sha256("".join(urls).encode("utf-8")).hexdigest()
    duration: timedelta = timedelta(weeks=4)
    expires: str = (datetime.utcnow() + duration).strftime("%a, %d %b %Y %H:%M:%S GMT")
    headers: Dict[str, str] = {
        "ETag": etag,
        "Cache-Control": f"public, max-age={int(duration.total_seconds())}, immutable",
        "Expires": expires,
    }

    return Response(content=encoded.tobytes(), media_type="image/jpeg", headers=headers)


handler = Mangum(app, lifespan="off")
