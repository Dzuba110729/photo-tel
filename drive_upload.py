#!/usr/bin/env python3
import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parent
PILOT = ROOT / "pilot-output" / "nanogpt" / "manifest.json"
PRIVATE = ROOT / ".gdrive"
CLIENT = PRIVATE / "client.json"
TOKEN = PRIVATE / "token.json"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
FOLDER_FIELDS = "id,name,mimeType,trashed,ownedByMe,shared,webViewLink"
FILE_FIELDS = "id,name,mimeType,parents,trashed,ownedByMe,shared,size,md5Checksum,sha256Checksum"
CHUNK_SIZE = 1024 * 1024
RETRIES = 3


@dataclass(frozen=True)
class UploadFile:
    path: Path
    mime_type: str
    size: int
    sha256: str
    md5: str

    @classmethod
    def read(cls, path, mime_type):
        data = path.read_bytes()
        return cls(path, mime_type, len(data), hashlib.sha256(data).hexdigest(),
                   hashlib.md5(data, usedforsecurity=False).hexdigest())

    @property
    def name(self):
        return self.path.name


def write_atomic(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_batch(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest["frames"]
    if not isinstance(frames, list) or not frames:
        raise ValueError("The manifest must contain at least one frame.")

    jpeg_dir = manifest_path.parent / "jpeg"
    files = {}
    table = io.StringIO(newline="")
    writer = csv.writer(table)
    writer.writerow(("filename", "sent_prompt", "model"))
    for frame in frames:
        relative = Path(frame["file"])
        name = relative.name
        if relative.parent != Path("jpeg") or relative.suffix.lower() not in (".jpg", ".jpeg"):
            raise ValueError("The manifest must reference JPEGs directly inside jpeg/.")
        if name in files:
            raise ValueError(f"Duplicate manifest entry: {name}")
        prompt, model = frame["sent_prompt"], frame["model"]
        if not all(isinstance(value, str) and value.strip() for value in (prompt, model)):
            raise ValueError(f"Missing sent prompt or model for {name}")
        item = UploadFile.read(jpeg_dir / name, "image/jpeg")
        if not item.size or ("jpeg_bytes" in frame and item.size != frame["jpeg_bytes"]):
            raise ValueError(f"JPEG size does not match the manifest: {name}")
        files[name] = item
        writer.writerow((name, prompt, model))

    jpeg_names = {path.name for path in jpeg_dir.iterdir()
                  if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg")}
    if jpeg_names != files.keys():
        raise ValueError("The manifest and jpeg/ contain different sets of JPEGs.")
    csv_path = manifest_path.parent / "prompts.csv"
    write_atomic(csv_path, table.getvalue().encode("utf-8"))
    files[csv_path.name] = UploadFile.read(csv_path, "text/csv")
    return files


def require_private_file(path):
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError(f"Use a regular file with mode 600 for {path.name}.")


def authorize():
    if not CLIENT.is_file():
        raise ValueError("Save your Google Desktop app OAuth JSON as .gdrive/client.json (see DRIVE.md).")
    require_private_file(CLIENT)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT), [SCOPE], autogenerate_code_verifier=True,
    )
    if flow.client_type != "installed":
        raise ValueError("Create an OAuth client of type Desktop app.")
    credentials = flow.run_local_server(
        host="127.0.0.1", port=0, timeout_seconds=300,
        authorization_prompt_message="", prompt="consent",
    )
    granted = credentials.granted_scopes or credentials.scopes or ()
    if not credentials.refresh_token or SCOPE not in granted:
        raise ValueError("Google did not grant offline access with the drive.file scope.")
    write_atomic(TOKEN, credentials.to_json().encode("utf-8"))


def open_drive():
    if not TOKEN.is_file():
        raise ValueError("Run python3 drive_upload.py --authorize in Terminal first (see DRIVE.md).")
    require_private_file(TOKEN)
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_authorized_user_file(str(TOKEN), [SCOPE])
    return build("drive", "v3", credentials=credentials)


def execute(request):
    return request.execute(num_retries=RETRIES)


def create_or_read(api, metadata, fields, *, media=None):
    from googleapiclient.errors import HttpError

    try:
        if media is None:
            execute(api.create(body=metadata, fields="id"))
        else:
            execute(api.create(body=metadata, fields="id", media_body=media))
    except HttpError as error:
        if error.resp.status != 409:
            raise
    return execute(api.get(fileId=metadata["id"], fields=fields))


def load_job(api, state_path, folder_name, files):
    signature = {name: item.sha256 for name, item in files.items()}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["folder_name"] != folder_name or state["sha256"] != signature:
            raise ValueError("The batch changed. Restore the original inputs or select a new --state.")
        return state

    ids = execute(api.generateIds(count=len(files) + 1, space="drive"))["ids"]
    if (len(ids) != len(files) + 1 or len(set(ids)) != len(ids)
            or any(not re.fullmatch(r"[\w-]+", file_id, re.ASCII) for file_id in ids)):
        raise ValueError("Drive returned invalid file IDs.")
    state = {"folder_id": ids[0], "folder_name": folder_name, "sha256": signature,
             "file_ids": dict(zip(files, ids[1:]))}
    write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
    return state


def list_files(api, folder_id):
    page = None
    while True:
        listing = execute(api.list(
            q=f"'{folder_id}' in parents and trashed = false", spaces="drive", pageSize=100,
            fields=f"nextPageToken,incompleteSearch,files({FILE_FIELDS})", pageToken=page,
        ))
        if listing.get("incompleteSearch"):
            raise ValueError("Drive returned an incomplete folder listing. Retry the batch.")
        yield from listing.get("files", [])
        page = listing.get("nextPageToken")
        if not page:
            return


def verify_file(remote, item, folder_id):
    sha256 = remote.get("sha256Checksum")
    matches_bytes = sha256 == item.sha256 if sha256 else remote.get("md5Checksum") == item.md5
    if (not matches_bytes or remote.get("size") != str(item.size)
            or remote.get("name") != item.name or remote.get("mimeType") != item.mime_type
            or remote.get("parents") != [folder_id] or remote.get("trashed") is not False
            or remote.get("ownedByMe") is not True
            or remote.get("shared") is not False):
        raise ValueError(f"Drive metadata or checksum does not match for {item.name}.")


def verify_folder(folder, folder_name):
    if (folder.get("name") != folder_name or folder.get("mimeType") != FOLDER_MIME
            or folder.get("trashed") is not False or folder.get("ownedByMe") is not True
            or folder.get("shared") is not False):
        raise ValueError("The destination must be a private folder owned by the current Drive user.")


def upload_batch(drive, files, *, state_path, folder_name, on_progress=None):
    """Upload a prepared batch. Keep state_path with the job so reruns use the same Drive IDs."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload

    api = drive.files()
    with state_path.with_suffix(state_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_job(api, state_path, folder_name, files)
        folder_id = state["folder_id"]
        file_ids = state["file_ids"]
        expected = {file_ids[name]: item for name, item in files.items()}
        try:
            folder = execute(api.get(fileId=folder_id, fields=FOLDER_FIELDS))
        except HttpError as error:
            if error.resp.status != 404:
                raise
            folder = create_or_read(api, {"id": folder_id, "name": folder_name,
                                          "mimeType": FOLDER_MIME}, FOLDER_FIELDS)
        verify_folder(folder, folder_name)

        existing = set()
        for remote in list_files(api, folder_id):
            file_id = remote["id"]
            if file_id not in expected or file_id in existing:
                raise ValueError("The Drive folder contains an unexpected or duplicate file.")
            verify_file(remote, expected[file_id], folder_id)
            existing.add(file_id)
        verified = len(existing)
        if on_progress is not None:
            on_progress(verified, folder["webViewLink"])

        for file_id, item in expected.items():
            if file_id in existing:
                continue
            metadata = {"id": file_id, "name": item.name, "parents": [folder_id],
                        "mimeType": item.mime_type}
            with item.path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item.sha256:
                    raise ValueError(f"The local file changed during the batch: {item.name}")
                stream.seek(0)
                media = MediaIoBaseUpload(stream, item.mime_type, chunksize=CHUNK_SIZE, resumable=True)
                remote = create_or_read(api, metadata, FILE_FIELDS, media=media)
            verify_file(remote, item, folder_id)
            verified += 1
            if on_progress is not None:
                on_progress(verified, folder["webViewLink"])

        listing = list(list_files(api, folder_id))
        if len(listing) != len(expected) or {remote["id"] for remote in listing} != expected.keys():
            raise ValueError("The Drive folder and the local batch contain different files. Retry the batch.")
        for remote in listing:
            verify_file(remote, expected[remote["id"]], folder_id)
        folder = execute(api.get(fileId=folder_id, fields=FOLDER_FIELDS))
        verify_folder(folder, folder_name)
        return {"folder_id": folder_id, "folder_url": folder["webViewLink"],
                "verified": len(listing), "reused": len(existing)}


def main():
    parser = argparse.ArgumentParser(description="Upload wallpaper JPEGs and prompts.csv to Google Drive.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Build the UTF-8 CSV locally without Drive access.")
    mode.add_argument("--authorize", action="store_true", help="Sign in through the browser and save local OAuth.")
    parser.add_argument("--manifest", type=Path, default=PILOT)
    parser.add_argument("--state", type=Path, default=PRIVATE / "pilot-job.json")
    parser.add_argument("--folder-name", default="Photo-tel — тестовые обои (10)")
    args = parser.parse_args()

    try:
        if args.authorize:
            authorize()
            print("Google Drive OAuth saved locally.")
            return 0
        files = prepare_batch(args.manifest)
        if args.prepare:
            print(f"Prepared {files['prompts.csv'].path} for {len(files) - 1} JPEGs.")
            return 0
        drive = open_drive()
        args.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = upload_batch(drive, files, state_path=args.state, folder_name=args.folder_name)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("Stopped. Rerun the same command to resume the batch.", file=sys.stderr)
        return 130
    except ImportError:
        print("Install requirements-drive.txt in the project virtual environment.", file=sys.stderr)
    except (ValueError, OSError, KeyError) as error:
        if isinstance(error, OSError) and not error.filename:
            print(f"Google Drive or local I/O failed ({type(error).__name__}). Retry the same command.",
                  file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
    except Exception as error:
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError

        if isinstance(error, HttpError):
            print(f"Google Drive API returned HTTP {error.resp.status}. See DRIVE.md.", file=sys.stderr)
        elif isinstance(error, RefreshError):
            print("Google OAuth renewal failed. Run --authorize with the same Google account.", file=sys.stderr)
        else:
            print(f"Google Drive operation failed ({type(error).__name__}). See DRIVE.md.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
