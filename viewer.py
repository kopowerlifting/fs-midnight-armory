import argparse
import json
import mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


STATIC_DIR = Path(__file__).parent / "viewer"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_profile_path(archive_dir: Path, path: str | None) -> str | None:
    if not path:
        return None

    profile_path = (archive_dir / path).resolve()
    if archive_dir.resolve() not in profile_path.parents or not profile_path.is_file():
        return None
    return profile_path.relative_to(archive_dir.resolve()).as_posix()


def _capture_list_item(record: dict, archive_dir: Path) -> dict | None:
    snapshot = record.get("snapshot") or {}
    profile_path = _relative_profile_path(
        archive_dir, snapshot.get("profile_data_file")
    )
    if profile_path is None:
        return None

    character = record.get("character") or {}
    profile = _read_json(archive_dir / profile_path)
    profile_character = profile.get("character") or {}
    realm = profile_character.get("realm") or {}
    class_info = profile_character.get("class") or {}
    active_specs = [
        specialization.get("name")
        for specialization in (profile_character.get("specs") or [])
        if specialization.get("active")
    ]
    return {
        "id": profile_path,
        "name": character.get("name") or profile_character.get("name", "Unknown"),
        "realm": realm.get("name", "Unknown realm"),
        "region": profile_character.get("region", "Unknown region"),
        "class": class_info.get("name") or record.get("class", "Unknown class"),
        "spec": record.get("spec") or ", ".join(active_specs),
        "captured_at": snapshot.get("captured_at"),
        "captured": snapshot.get("captured"),
        "average_item_level": profile_character.get("averageItemLevel"),
        "profile_data_file": profile_path,
        "appearances": record.get(
            "appearances",
            [{"class": record.get("class"), "spec": record.get("spec")}],
        ),
    }


def load_capture_catalog(archive_dir: Path) -> list[dict]:
    records_by_profile = {}
    index_path = archive_dir / "capture_index.json"
    if index_path.is_file():
        for record in _read_json(index_path).get("captures", []):
            profile_path = (record.get("snapshot") or {}).get("profile_data_file")
            if profile_path:
                records_by_profile[profile_path] = record

    for spec_path in archive_dir.glob("*/*.json"):
        if spec_path.name in {"capture_index.json", "all_builds.json"}:
            continue
        if spec_path.parent.name == "talent_layouts":
            continue
        for record in _read_json(spec_path):
            snapshot = record.get("snapshot") or {}
            profile_path = snapshot.get("profile_data_file")
            if not profile_path:
                continue
            existing = records_by_profile.get(profile_path)
            if existing is None:
                records_by_profile[profile_path] = record
            else:
                appearances = existing.setdefault("appearances", [])
                appearance = {
                    "class": record.get("class"),
                    "spec": record.get("spec"),
                }
                if appearance not in appearances:
                    appearances.append(appearance)
    for capture_root in ("armory", "captures"):
        root = archive_dir / capture_root
        if not root.is_dir():
            continue
        for profile_file in root.rglob("*.json"):
            try:
                profile = _read_json(profile_file)
            except (OSError, json.JSONDecodeError) as error:
                print(f"Skipping unreadable profile capture {profile_file}: {error}")
                continue
            if not isinstance(profile, dict) or not isinstance(
                profile.get("character"), dict
            ):
                continue

            relative_path = profile_file.relative_to(archive_dir).as_posix()
            if relative_path in records_by_profile:
                continue
            captured = 0
            captured_at = None
            if capture_root == "captures":
                try:
                    captured_datetime = datetime.strptime(
                        profile_file.stem, "%Y%m%dT%H%M%S%fZ"
                    ).replace(tzinfo=timezone.utc)
                    captured = captured_datetime.timestamp()
                    captured_at = captured_datetime.isoformat()
                except ValueError:
                    pass
            profile_character = profile["character"]
            records_by_profile[relative_path] = {
                "character": {"name": profile_character.get("name")},
                "snapshot": {
                    "profile_data_file": relative_path,
                    "captured_at": captured_at,
                    "captured": captured,
                },
                "appearances": [],
            }

    items = []
    for record in records_by_profile.values():
        item = _capture_list_item(record, archive_dir)
        if item is not None:
            items.append(item)

    items.sort(
        key=lambda item: (
            item.get("captured") or 0,
            item["name"].casefold(),
            item["realm"].casefold(),
        ),
        reverse=True,
    )
    for item in items:
        item["id"] = item["profile_data_file"]
    return items


def create_handler(archive_dir: Path):
    class ArchiveRequestHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            if parsed_url.path in STATIC_FILES:
                filename, content_type = STATIC_FILES[parsed_url.path]
                body = (STATIC_DIR / filename).read_bytes()
                self._send(200, body, content_type)
                return

            if parsed_url.path == "/api/captures":
                self._send_json(200, load_capture_catalog(archive_dir))
                return

            if parsed_url.path == "/api/talent-layout":
                query = parse_qs(parsed_url.query)
                class_slug = query.get("class", [""])[0]
                spec_slug = query.get("spec", [""])[0]
                index_path = archive_dir / "talent_layouts" / "index.json"
                if not index_path.is_file():
                    self._send_json(
                        404,
                        {"error": "No talent layouts have been imported yet"},
                    )
                    return

                index = _read_json(index_path)
                entry = next(
                    (
                        layout
                        for layout in index.get("layouts", [])
                        if layout.get("class") == class_slug
                        and layout.get("spec") == spec_slug
                    ),
                    None,
                )
                if entry is None:
                    self._send_json(
                        404,
                        {
                            "error": (
                                f"No saved talent layout for "
                                f"{class_slug}/{spec_slug}"
                            )
                        },
                    )
                    return

                layout_path = _relative_profile_path(archive_dir, entry.get("path"))
                if layout_path is None:
                    self._send_json(
                        404, {"error": "Talent layout file is missing"}
                    )
                    return
                self._send_json(200, _read_json(archive_dir / layout_path))
                return

            if parsed_url.path == "/api/capture":
                query = parse_qs(parsed_url.query)
                capture_id = query.get("id", [""])[0]
                record = next(
                    (
                        item
                        for item in load_capture_catalog(archive_dir)
                        if item["id"] == capture_id
                    ),
                    None,
                )
                if record is None:
                    self._send_json(404, {"error": "Capture not found"})
                    return
                profile_path = _relative_profile_path(
                    archive_dir, record["profile_data_file"]
                )

                if profile_path is None:
                    self._send_json(404, {"error": "Profile data file not found"})
                    return
                profile_file = Path(profile_path)
                manifest_path = archive_dir / profile_file.with_name(
                    profile_file.stem + ".assets.json"
                )
                assets = (
                    _read_json(manifest_path).get("assets", {})
                    if manifest_path.is_file()
                    else {}
                )
                self._send_json(
                    200,
                    {
                        "profile": _read_json(archive_dir / profile_path),
                        "assets": assets,
                    },
                )
                return

            if parsed_url.path == "/api/asset":
                query = parse_qs(parsed_url.query)
                asset_path = query.get("path", [""])[0]
                if not asset_path.startswith("assets/"):
                    self._send_json(404, {"error": "Asset not found"})
                    return
                relative_path = _relative_profile_path(archive_dir, asset_path)
                if relative_path is None:
                    self._send_json(404, {"error": "Asset not found"})
                    return
                file_path = archive_dir / relative_path
                content_type = mimetypes.guess_type(file_path.name)[0]
                if content_type is None or not content_type.startswith("image/"):
                    self._send_json(404, {"error": "Asset not found"})
                    return
                self._send(200, file_path.read_bytes(), content_type)
                return

            self._send_json(404, {"error": "Not found"})

        def log_message(self, format: str, *args) -> None:
            print(f"{self.address_string()} - {format % args}")

    return ArchiveRequestHandler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the local, read-only WoW armory archive viewer."
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "wow_archive",
        help=(
            "Archive directory created by scraper.py "
            "(default: wow_archive beside viewer.py)."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen address (use 0.0.0.0 to allow access from your LAN).",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    archive_dir = args.archive_dir.resolve()
    if not archive_dir.is_dir():
        parser.error(f"Archive directory does not exist: {archive_dir}")

    server = ThreadingHTTPServer(
        (args.host, args.port), create_handler(archive_dir)
    )
    print(f"Armory archive viewer: http://{args.host}:{args.port}/")
    print(f"Archive: {archive_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping viewer.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
