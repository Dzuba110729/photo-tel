"""Local HTTP entry point. Start with uvicorn wallpaper_app:app --host 127.0.0.1."""

import fcntl
import json
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pilot import ESTIMATED_IMAGE_USD, ROOT
from wallpaper_jobs import NanoGPT, OrderError, Orders, OrderWorker, elapsed, open_order_drive
from wallpaper_prompts import DEFAULT_TONE, PRESETS


DATA_DIR = ROOT / "orders-data"
MAX_COUNT = 100
FrameCount = Annotated[int, Field(strict=True, ge=1, le=MAX_COUNT)]


def normalize(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


class OrderInput(BaseModel, extra="forbid"):
    presets: list[str] = Field(default_factory=list, max_length=len(PRESETS))
    custom: list[str] = Field(default_factory=list, max_length=MAX_COUNT)
    count: FrameCount
    tone: Literal["light", "dark"] = DEFAULT_TONE
    quantities: list[FrameCount] | None = Field(default=None, max_length=MAX_COUNT)

    @field_validator("custom")
    @classmethod
    def clean_custom(cls, values):
        themes = [normalize(value) for value in values]
        if any(not value or len(value) > 160 or any(not char.isprintable() for char in value) for value in themes):
            raise ValueError("Каждая своя тема должна содержать от 1 до 160 печатных символов.")
        return themes

    @model_validator(mode="after")
    def check_themes(self):
        if any(key not in PRESETS for key in self.presets):
            raise ValueError("Выберите готовые темы из списка.")
        names = [PRESETS[key][0] for key in self.presets] + self.custom
        if not names:
            raise ValueError("Выберите или добавьте хотя бы одну тему.")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Каждая тема должна встречаться один раз.")
        if self.count < len(names):
            raise ValueError("Количество кадров должно быть не меньше количества тем.")
        if self.quantities is not None:
            if len(self.quantities) != len(names):
                raise ValueError("Укажите количество кадров для каждой выбранной темы.")
            if sum(self.quantities) != self.count:
                raise ValueError("Сумма по темам должна совпадать с количеством кадров в заказе.")
        return self


def create_app(directory=DATA_DIR, *, nano_factory=NanoGPT, drive_factory=open_order_drive):
    orders = Orders(directory)

    @asynccontextmanager
    async def lifespan(app):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / ".app.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("The Photo-tel app is already running for this data directory.") from error
            orders.initialize()
            worker = OrderWorker(orders, nano_factory, drive_factory)
            app.state.worker = worker
            worker.start()
            try:
                yield
            finally:
                worker.close()

    app = FastAPI(title="Photo-tel", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")

    def summary(order_id):
        with orders.connect() as db:
            order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise HTTPException(404, "Заказ не найден.")
            rows = db.execute(
                "SELECT theme, state, count(*) AS amount FROM frames WHERE order_id = ? "
                "GROUP BY theme, state ORDER BY min(number)", (order_id,),
            ).fetchall()
        result = dict(order)
        result["selection"] = {"tone": DEFAULT_TONE, "quantities": None, **json.loads(result["selection"])}
        counts = {}
        for row in rows:
            counts[row["theme"]] = counts.get(row["theme"], 0) + row["amount"]
        result["themes"] = counts
        result["generated"] = sum(row["amount"] for row in rows if row["state"] in ("received", "ready"))
        result["jpeg_ready"] = sum(row["amount"] for row in rows if row["state"] == "ready")
        result["elapsed_seconds"] = (
            elapsed(order["created_at"], order["finished_at"]) if order["finished_at"] else None
        )
        return result

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(Path(__file__).with_name("wallpaper_ui.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/api/options")
    def options():
        return {"presets": {key: item[0] for key, item in PRESETS.items()},
                "price_usd": str(ESTIMATED_IMAGE_USD), "max_count": MAX_COUNT}

    @app.get("/api/orders")
    def recent_orders():
        return orders.recent()

    @app.put("/api/orders/{order_id}", status_code=202)
    def create_order(order_id: UUID, selection: OrderInput, request: Request):
        order_id = str(order_id)
        try:
            orders.create(order_id, selection.model_dump())
        except OrderError as error:
            raise HTTPException(409, str(error)) from error
        request.app.state.worker.wake.set()
        return summary(order_id)

    @app.get("/api/orders/{order_id}")
    def order_status(order_id: UUID):
        return summary(str(order_id))

    @app.post("/api/orders/{order_id}/resume", status_code=202)
    def resume_order(order_id: UUID, request: Request):
        order_id = str(order_id)
        if not orders.retry(order_id):
            current = summary(order_id)
            if current["status"] not in ("queued", "running", "done"):
                raise HTTPException(409, current["error"] or "Заказ пока нельзя возобновить.")
        request.app.state.worker.wake.set()
        return summary(order_id)

    return app


app = create_app()
