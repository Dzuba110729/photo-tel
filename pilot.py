#!/usr/bin/env python3
import argparse
import base64
import fcntl
import getpass
import json
import os
import shutil
import stat
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / ".nanogpt-key"
KEY_ENV = "NANOGPT_API_KEY"
OUTPUT = ROOT / "pilot-output" / "nanogpt"
MODEL = "seedream-v5.0-lite"
API_HOST = "https://api.nano-gpt.com"
GENERATION_PATH = "/api/v1/images/generations"
REQUEST_SIZE = "1872x4064"
FALLBACK_SIZE = "2304x4096"
JPEG_SIZE = (1870, 4063)
JPEG_QUALITY = 95
ESTIMATED_IMAGE_USD = Decimal("0.035")
BUDGET_USD = Decimal("1.00")
FRAME_INSTRUCTIONS = (
    "Portrait phone lock-screen wallpaper, edge-to-edge artwork. "
    "Keep the top center visually quiet for the system clock. "
    "No text, numbers, clock, logos, watermark, border or phone mockup."
)


def save_key(*, replace=False):
    if not sys.stdin.isatty():
        raise ValueError("Run --set-key interactively in Terminal.")
    if KEY_FILE.exists() and not replace:
        raise ValueError(f"{KEY_FILE.name} already exists. Use --replace-key in Terminal.")
    key = getpass.getpass(f"{KEY_ENV}: ").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("Paste the complete NanoGPT API key as one line.")
    if replace:
        balance = check_balance(key)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=KEY_FILE.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(key + "\n")
                file.close()
                temporary.replace(KEY_FILE)
            finally:
                temporary.unlink(missing_ok=True)
        print(f"NanoGPT USD balance: ${balance}")
        print(f"Saved {KEY_ENV} locally in {KEY_FILE.name} (mode 600).")
        return
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(key + "\n")
    print(f"Saved {KEY_ENV} locally in {KEY_FILE.name} (mode 600).")


def load_key():
    key = os.environ.get(KEY_ENV, "").strip()
    if not key and KEY_FILE.exists():
        permissions = KEY_FILE.lstat().st_mode
        if not stat.S_ISREG(permissions) or permissions & 0o077:
            raise ValueError(f"Set {KEY_FILE.name} permissions to 600 and use a regular file.")
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError(f"{KEY_ENV} is missing. Run python3 pilot.py --set-key in Terminal.")
    if any(character.isspace() for character in key):
        raise ValueError(f"{KEY_ENV} must be a single line without whitespace.")
    return key


def save_json(path, data):
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def api_post(path, key, payload=None, timeout=30):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{API_HOST}{path}", data=body, headers=headers, method="POST")
    if path == "/api/check-balance":
        request.add_unredirected_header("x-api-key", key)
    else:
        request.add_unredirected_header("Authorization", f"Bearer {key}")
    with urlopen(request, timeout=timeout) as response:
        return json.load(response), response.headers.get("x-request-id")


def check_balance(key):
    result, _ = api_post("/api/check-balance", key)
    return Decimal(str(result["usd_balance"]))


def save_original(image, name, directory=None):
    if directory is None:
        directory = OUTPUT / "originals"
    existing = next(directory.glob(f"{name}.*"), None)
    if existing is not None:
        return existing

    temporary = directory / f".{name}.part"
    if image.get("b64_json"):
        temporary.write_bytes(base64.b64decode(image["b64_json"], validate=True))
    else:
        url = image["url"]
        if urlsplit(url).scheme != "https":
            raise ValueError("Expected an HTTPS image URL from NanoGPT.")
        with urlopen(url, timeout=90) as response, temporary.open("wb") as file:
            shutil.copyfileobj(response, file)

    with Image.open(temporary) as original:
        extension = Image.registered_extensions()
        image_format = original.format
        suffix = next((suffix for suffix, fmt in extension.items() if fmt == image_format), None)
        if suffix is None:
            raise ValueError("Unknown format in NanoGPT image response.")
        original.verify()
    destination = directory / (name + (".jpg" if image_format == "JPEG" else suffix))
    temporary.replace(destination)
    return destination


def verify_jpeg(path):
    with Image.open(path) as image:
        if image.format != "JPEG" or image.size != JPEG_SIZE or image.mode != "RGB":
            raise ValueError(f"Unexpected JPEG format, size or color mode in {path.name}.")
        image.load()


def convert_image(source, destination):
    with Image.open(source) as original:
        source_size = original.size
        source_format = original.format
        image = ImageOps.exif_transpose(original).convert("RGB")

    width, height = image.size
    target_width, target_height = JPEG_SIZE
    if (width, height) == (2304, 4096) or (
        0 <= width - target_width <= 16 and 0 <= height - target_height <= 16
    ):
        left = (width - target_width) // 2
        top = (height - target_height) // 2
        crop = (left, top, left + target_width, top + target_height)
        image = image.crop(crop)
        transform = {"method": "crop", "crop_box": crop, "scale": 1}
    else:
        image = ImageOps.fit(image, JPEG_SIZE, method=Image.Resampling.LANCZOS)
        scale = max(target_width / width, target_height / height)
        transform = {"method": "center_crop_lanczos", "scale": scale}

    temporary = destination.with_suffix(".jpg.part")
    image.save(temporary, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    verify_jpeg(temporary)
    temporary.replace(destination)
    return {"source_format": source_format, "source_size": source_size,
            "oriented_size": (width, height), "jpeg_size": JPEG_SIZE,
            "quality": JPEG_QUALITY, "transform": transform, "jpeg_bytes": destination.stat().st_size}


def make_payload(frame, size):
    return {
        "model": MODEL,
        "prompt": f"{FRAME_INSTRUCTIONS} {frame['prompt']}",
        "size": size,
        "n": 1,
        "response_format": "url",
    }


def submit_frame(record, record_path, payload, key, planned_price):
    balance = check_balance(key)
    if balance < planned_price:
        raise ValueError(f"USD balance ${balance} is below the estimated frame cost ${planned_price}.")
    attempt = {"input": payload, "started_at_utc": utc_now(), "balance_before_usd": str(balance)}
    record["attempts"].append(attempt)
    save_json(record_path, record)
    started = time.monotonic()
    try:
        result, request_id = api_post(GENERATION_PATH, key, payload, timeout=180)
    except HTTPError as error:
        detail = error.read(8192).decode("utf-8", errors="replace").replace(key, "[redacted]")
        rejected = error.code in {400, 401, 402, 403, 404, 413, 422, 429}
        size_rejected = error.code in {400, 422} and any(
            word in detail.lower() for word in ("size", "resolution", "aspect ratio", "dimensions", "pixel")
        )
        attempt.update(http_status=error.code, rejected=rejected, size_rejected=size_rejected,
                       error=detail, api_seconds=round(time.monotonic() - started, 3))
        save_json(record_path, record)
        if size_rejected and payload["size"] == REQUEST_SIZE:
            print(f"{record['frame']}: custom size rejected, trying {FALLBACK_SIZE}", flush=True)
            submit_frame(record, record_path, {**payload, "size": FALLBACK_SIZE}, key, planned_price)
            return
        raise

    attempt.update(api_seconds=round(time.monotonic() - started, 3), result_at_utc=utc_now())
    record.update(input=payload, response=result,
                  request_id=request_id or result.get("requestId") or result.get("request_id"))
    save_json(record_path, record)


def read_billing(key, record):
    result = record["response"]
    before = Decimal(record["attempts"][-1]["balance_before_usd"])
    after = check_balance(key)
    delta = before - after
    cost = result.get("cost")
    payment_source = result.get("paymentSource")
    if cost is not None and payment_source == "USD":
        charge, source = Decimal(str(cost)), "nanogpt_response"
    elif delta > 0:
        charge, source = delta, "usd_balance_delta"
    else:
        charge, source = None, None
    return {"api_cost": cost, "payment_source": payment_source, "charge_source": source,
            "charge_usd": str(charge) if charge is not None else None,
            "balance_before_usd": str(before), "balance_after_usd": str(after),
            "balance_delta_usd": str(delta)}


def run_frame(frame, key, size, spent, planned_price):
    name = frame["id"]
    record_path = OUTPUT / "records" / f"{name}.json"
    jpeg_path = OUTPUT / "jpeg" / f"{name}.jpg"
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
    else:
        record = {"frame": name, "theme": frame["theme"], "model": MODEL, "provider": "NanoGPT",
                  "estimated_usd": str(ESTIMATED_IMAGE_USD), "attempts": []}
    if record.get("finished_at_utc"):
        verify_jpeg(jpeg_path)
        print(f"{name}: already verified", flush=True)
        return record
    if "response" not in record:
        if record["attempts"]:
            last_attempt = record["attempts"][-1]
            if not last_attempt.get("rejected"):
                raise ValueError(f"{name}: submission outcome unknown. Check NanoGPT Usage before retrying.")
            if last_attempt.get("size_rejected"):
                size = FALLBACK_SIZE
        if spent + planned_price > BUDGET_USD:
            raise ValueError("The next generation would exceed the $1 pilot budget at the observed rate.")
        submit_frame(record, record_path, make_payload(frame, size), key, planned_price)

    result = record["response"]
    images = result.get("data") or []
    if len(images) != 1:
        raise ValueError(f"{name}: expected one image in the NanoGPT response.")
    image = images[0]
    if "files" not in record:
        local_start = time.monotonic()
        original_path = save_original(image, name)
        record["generation"] = {
            "reported_prompt": (image.get("revised_prompt") or result.get("revised_prompt")
                                or image.get("prompt") or result.get("prompt")),
            "reported_model": result.get("model"),
            "reported_seed": image.get("seed", result.get("seed")),
        }
        record["files"] = {
            "original": str(original_path.relative_to(OUTPUT)),
            "jpeg": str(jpeg_path.relative_to(OUTPUT)),
            **convert_image(original_path, jpeg_path),
        }
        record["download_convert_verify_seconds"] = round(time.monotonic() - local_start, 3)
        save_json(record_path, record)
    else:
        verify_jpeg(jpeg_path)

    record["billing"] = read_billing(key, record)
    record["finished_at_utc"] = utc_now()
    started = datetime.fromisoformat(record["attempts"][0]["started_at_utc"])
    finished = datetime.fromisoformat(record["finished_at_utc"])
    record["elapsed_seconds"] = round((finished - started).total_seconds(), 3)
    save_json(record_path, record)
    files = record["files"]
    charge = record["billing"]["charge_usd"]
    cost_label = f"${charge} USD" if charge is not None else "charge pending verification"
    print(f"{name}: {files['source_format']} {files['source_size']} → JPEG {JPEG_SIZE}, "
          f"{record['elapsed_seconds']:.2f} s, {cost_label}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description="Run the NanoGPT Seedream 5.0 Lite wallpaper pilot.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--set-key", action="store_true", help="save the key locally via hidden input")
    modes.add_argument("--replace-key", action="store_true", help="validate and replace the local key via hidden input")
    modes.add_argument("--plan", action="store_true", help="show prompts and estimate without network calls")
    modes.add_argument("--check-balance", action="store_true", help="read the USD balance without generating images")
    parser.add_argument("--count", type=int, choices=range(1, 11), default=1,
                        help="total number of planned frames to process, including earlier runs (default: 1)")
    parser.add_argument("--size", choices=(REQUEST_SIZE, FALLBACK_SIZE),
                        help="select the request size for new frames (defaults to the first successful size)")
    args = parser.parse_args()
    if args.set_key or args.replace_key:
        save_key(replace=args.replace_key)
        return
    if args.check_balance:
        print(f"NanoGPT USD balance: ${check_balance(load_key())}")
        return

    frames = json.loads((ROOT / "pilot-prompts.json").read_text(encoding="utf-8"))[:args.count]
    estimate = ESTIMATED_IMAGE_USD * len(frames)
    if estimate > BUDGET_USD:
        raise ValueError("Estimated cost exceeds the $1 pilot budget.")
    size = args.size or REQUEST_SIZE
    print(f"{len(frames)} frame(s). Catalog estimate: ${estimate:.3f} USD.", flush=True)
    if args.plan:
        print(json.dumps([{"id": frame["id"], **make_payload(frame, size)} for frame in frames], indent=2))
        return

    key = load_key()
    for directory in ("records", "originals", "jpeg"):
        (OUTPUT / directory).mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spent = Decimal(0)
        planned_price = ESTIMATED_IMAGE_USD
        for frame in frames:
            record = run_frame(frame, key, size, spent, planned_price)
            charge = record["billing"]["charge_usd"]
            spent += Decimal(charge) if charge is not None else planned_price
            if charge is not None:
                planned_price = max(planned_price, Decimal(charge))
            if not args.size:
                size = record["input"]["size"]


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        print(f"NanoGPT HTTP {error.code}. Check the account, balance or the local request record.",
              file=sys.stderr)
        sys.exit(1)
    except BlockingIOError:
        sys.exit("Pilot is already running in another process.")
    except (ValueError, KeyError, URLError, OSError) as error:
        sys.exit(str(error))
