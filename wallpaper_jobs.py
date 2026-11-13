"""Persistent order queue and adapters for NanoGPT and Google Drive."""

import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from threading import Event, Thread
from urllib.error import HTTPError

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

import drive_upload
import pilot
from wallpaper_prompts import DEFAULT_TONE, plan_frames


PARALLEL_IMAGES = 3
REJECTED_HTTP = {400, 401, 402, 403, 404, 413, 422, 429}
LOGGER = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, selection TEXT NOT NULL, count INTEGER NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'queued', error TEXT,
    uploaded INTEGER NOT NULL DEFAULT 0, folder_url TEXT, upload_seconds REAL,
    balance_before TEXT, balance_after TEXT
);
CREATE TABLE IF NOT EXISTS frames (
    order_id TEXT NOT NULL REFERENCES orders(id), number INTEGER NOT NULL,
    theme TEXT NOT NULL, filename TEXT NOT NULL, prompt TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    response TEXT, request_id TEXT, api_seconds REAL, image_details TEXT,
    submitted_at TEXT, received_at TEXT, ready_at TEXT,
    PRIMARY KEY (order_id, number), UNIQUE (order_id, filename), UNIQUE (order_id, prompt)
);
"""


class OrderError(Exception):
    pass


class Orders:
    def __init__(self, directory):
        self.directory = directory
        self.db_path = directory / "orders.sqlite3"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    def create(self, order_id, selection):
        selection = {"tone": DEFAULT_TONE, "quantities": None, **selection}
        saved_selection = json.dumps(selection, ensure_ascii=False)
        with self.connect() as db:
            inserted = db.execute(
                "INSERT INTO orders (id, selection, count, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (order_id, saved_selection, selection["count"], pilot.utc_now()),
            ).rowcount
            if not inserted:
                saved = db.execute("SELECT selection FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
                if {"tone": DEFAULT_TONE, "quantities": None, **json.loads(saved)} != selection:
                    raise OrderError("Этот идентификатор уже принадлежит другому заказу.")
                return
            frames = plan_frames(order_id, **selection)
            db.executemany(
                "INSERT INTO frames (order_id, number, theme, filename, prompt) VALUES (?, ?, ?, ?, ?)",
                ((order_id, frame["number"], frame["theme"], frame["filename"], frame["prompt"])
                 for frame in frames),
            )

    def get(self, order_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

    def frames(self, order_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM frames WHERE order_id = ? ORDER BY number", (order_id,)).fetchall()

    def recent(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, count, created_at, status FROM orders ORDER BY rowid DESC LIMIT 30"
            )]

    def next_id(self):
        with self.connect() as db:
            row = db.execute("SELECT id FROM orders WHERE status IN ('queued','running') ORDER BY rowid LIMIT 1").fetchone()
            return row["id"] if row else None

    def update_order(self, order_id, **values):
        columns = ", ".join(f"{column} = ?" for column in values)
        with self.connect() as db:
            db.execute(f"UPDATE orders SET {columns} WHERE id = ?", (*values.values(), order_id))

    def update_frame(self, frame, **values):
        columns = ", ".join(f"{column} = ?" for column in values)
        with self.connect() as db:
            db.execute(f"UPDATE frames SET {columns} WHERE order_id = ? AND number = ?",
                       (*values.values(), frame["order_id"], frame["number"]))

    def retry(self, order_id):
        with self.connect() as db:
            return db.execute(
                "UPDATE orders SET status = 'queued', error = NULL WHERE id = ? AND status = 'failed'",
                (order_id,),
            ).rowcount


class NanoGPT:
    def __init__(self):
        self.key = pilot.load_key()

    def balance(self):
        return pilot.check_balance(self.key)

    def generate(self, prompt):
        payload = {"model": pilot.MODEL, "prompt": prompt, "size": pilot.REQUEST_SIZE,
                   "n": 1, "response_format": "url"}
        return pilot.api_post(pilot.GENERATION_PATH, self.key, payload, timeout=180)


def open_order_drive():
    drive = drive_upload.open_drive()
    drive_upload.execute(drive.files().list(pageSize=1, spaces="drive", fields="nextPageToken"))
    return drive


def elapsed(start, finish):
    return round((datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds(), 3)


def usd_cost(response):
    cost = response.get("cost")
    if cost is not None and response.get("paymentSource") == "USD":
        return Decimal(str(cost))
    return None


def describe_error(error, stage):
    if isinstance(error, OrderError):
        return str(error)
    if isinstance(error, RefreshError):
        return "Истекла авторизация Google Диска. Выполните drive_upload.py --authorize и возобновите заказ."
    if isinstance(error, HttpError):
        return f"{stage}. Google Диск вернул HTTP {error.resp.status}."
    if isinstance(error, HTTPError):
        return f"{stage}. HTTP {error.code}."
    return f"{stage}. {type(error).__name__}."


class OrderWorker:
    def __init__(self, orders, nano_factory=NanoGPT, drive_factory=open_order_drive):
        self.orders = orders
        self.nano_factory = nano_factory
        self.drive_factory = drive_factory
        self.wake = Event()
        self.closing = Event()
        self.thread = Thread(target=self.work, name="orders")

    def start(self):
        self.thread.start()

    def close(self):
        self.closing.set()
        self.wake.set()
        self.thread.join()

    def work(self):
        while not self.closing.is_set():
            self.wake.clear()
            order_id = self.orders.next_id()
            if order_id:
                self.run(order_id)
            else:
                self.wake.wait()

    def run(self, order_id):
        order = self.orders.get(order_id)
        self.orders.update_order(order_id, status="running", started_at=order["started_at"] or pilot.utc_now())
        stage = "Подготовка заказа"
        try:
            frames = self.orders.frames(order_id)
            if any(frame["state"] == "submitting" for frame in frames):
                raise OrderError("Требуется сверка запроса в NanoGPT Usage.")
            stage = "Подключение к Google Диску"
            drive = self.drive_factory()
            directory = self.orders.directory / order_id
            for name in ("originals", "jpeg"):
                (directory / name).mkdir(parents=True, exist_ok=True, mode=0o700)

            stage = "Проверка баланса NanoGPT"
            nano = None
            pending = sum(frame["state"] == "pending" for frame in frames)
            if pending and not self.closing.is_set():
                nano = self.nano_factory()
                balance = nano.balance()
                expected = pilot.ESTIMATED_IMAGE_USD * pending
                if balance < expected:
                    raise OrderError(f"Для {pending} кадров нужно около ${expected:.3f}. На балансе ${balance:.3f}.")
                if order["balance_before"] is None:
                    self.orders.update_order(order_id, balance_before=str(balance))

            stage = "Генерация или подготовка JPEG"
            self.process_frames(directory, frames, nano)
            if self.closing.is_set():
                return

            stage = "Загрузка и проверка файлов на Google Диске"
            frames = self.orders.frames(order_id)
            started = time.monotonic()
            result = self.upload(order_id, directory, frames, drive)
            upload_seconds = round(time.monotonic() - started, 3)
            balance_after = None
            try:
                balance_after = str((nano or self.nano_factory()).balance())
            except Exception as error:
                LOGGER.warning("Balance reconciliation for %s failed (%s)", order_id, type(error).__name__)
            self.orders.update_order(
                order_id, status="done", error=None, finished_at=pilot.utc_now(), balance_after=balance_after,
                uploaded=result["verified"], folder_url=result["folder_url"], upload_seconds=upload_seconds,
            )
        except Exception as error:
            if isinstance(error, InterruptedError) and self.closing.is_set():
                return
            unknown = [frame["filename"] for frame in self.orders.frames(order_id)
                       if frame["state"] == "submitting"]
            message = describe_error(error, stage)
            if unknown:
                files = ", ".join(unknown)
                message = f"Исход платных запросов неизвестен ({files}). Проверьте NanoGPT Usage."
            self.orders.update_order(order_id, status="blocked" if unknown else "failed", error=message)
            LOGGER.warning("Order %s failed during %s (%s)", order_id, stage, type(error).__name__)

    def process_frames(self, directory, frames, nano):
        stopped = Event()

        def process(frame):
            if stopped.is_set() or self.closing.is_set():
                return
            try:
                self.process_frame(directory, frame, nano)
            except Exception:
                stopped.set()
                raise

        work = [frame for frame in frames if frame["state"] != "ready"]
        if work:
            with ThreadPoolExecutor(max_workers=PARALLEL_IMAGES) as pool:
                for _ in pool.map(process, work):
                    pass

    def process_frame(self, directory, frame, nano):
        name = frame["filename"]
        if frame["state"] == "pending":
            self.orders.update_frame(frame, state="submitting", submitted_at=pilot.utc_now())
            start = time.monotonic()
            try:
                response, request_id = nano.generate(frame["prompt"])
            except HTTPError as error:
                if error.code in REJECTED_HTTP:
                    self.orders.update_frame(frame, state="pending")
                    raise OrderError(f"NanoGPT отклонил {name} (HTTP {error.code}).") from error
                raise
            self.orders.update_frame(
                frame, state="received", response=json.dumps(response),
                request_id=request_id or response.get("requestId") or response.get("request_id"),
                api_seconds=round(time.monotonic() - start, 3), received_at=pilot.utc_now(),
            )
        else:
            response = json.loads(frame["response"])

        jpeg = directory / "jpeg" / name
        if jpeg.is_file():
            pilot.verify_jpeg(jpeg)
            details = {"jpeg_bytes": jpeg.stat().st_size, "quality": pilot.JPEG_QUALITY}
        else:
            images = response.get("data") or []
            if len(images) != 1:
                raise OrderError(f"В ответе NanoGPT для {name} отсутствует единственный ожидаемый кадр.")
            try:
                original = pilot.save_original(images[0], jpeg.stem, directory / "originals")
            except HTTPError as error:
                raise OrderError(f"Скачивание {name} вернуло HTTP {error.code}. Ответ NanoGPT сохранён.") from error
            details = pilot.convert_image(original, jpeg)
        self.orders.update_frame(frame, state="ready", image_details=json.dumps(details), ready_at=pilot.utc_now())

    def upload(self, order_id, directory, frames, drive):
        manifest = directory / "manifest.json"
        entries = []
        for frame in frames:
            response = json.loads(frame["response"])
            image = response["data"][0]
            cost = usd_cost(response)
            entries.append({
                "file": f"jpeg/{frame['filename']}", "theme": frame["theme"],
                "sent_prompt": frame["prompt"], "model": pilot.MODEL, "request_id": frame["request_id"],
                "api_seconds": frame["api_seconds"], "charge_usd": str(cost) if cost is not None else None,
                "submitted_at": frame["submitted_at"], "received_at": frame["received_at"],
                "ready_at": frame["ready_at"],
                "reported_prompt": image.get("revised_prompt") or response.get("revised_prompt")
                                   or image.get("prompt") or response.get("prompt"),
                "reported_seed": image.get("seed", response.get("seed")),
                **json.loads(frame["image_details"]),
            })
        pilot.save_json(manifest, {"order_id": order_id, "frames": entries})
        files = drive_upload.prepare_batch(manifest)

        def progress(verified, url):
            self.orders.update_order(order_id, uploaded=verified, folder_url=url)
            if self.closing.is_set():
                raise InterruptedError

        return drive_upload.upload_batch(
            drive, files, state_path=directory / "drive-state.json", on_progress=progress,
            folder_name=f"Photo-tel · {len(frames)} · {order_id[:8]}",
        )
