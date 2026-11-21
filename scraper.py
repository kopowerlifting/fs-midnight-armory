import argparse
import hashlib
import json
import mimetypes
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://worldofwarcraft.blizzard.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    )
}

TOP_PLAYERS_PER_SPEC = 2
REQUEST_DELAY_SECONDS = 1
OUTPUT_DIR = Path("wow_archive")
OUTPUT_DIR.mkdir(exist_ok=True)

PROFILE_STATE_SCRIPT_ID = "character-profile-mount-initial-state"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_HOSTS = {
    "render.worldofwarcraft.com",
    "bnetcmsus-a.akamaihd.net",
    "bnetcmsus-b.akamaihd.net",
}
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}

CLASSES = {
    "deathknight": ["blood", "frost", "unholy"],
    "warrior": ["arms", "fury", "protection"],
    "paladin": ["holy", "protection", "retribution"],
    "hunter": ["beastmastery", "marksmanship", "survival"],
    "rogue": ["assassination", "outlaw", "subtlety"],
    "priest": ["discipline", "holy", "shadow"],
    "shaman": ["elemental", "enhancement", "restoration"],
    "mage": ["arcane", "fire", "frost"],
    "warlock": ["affliction", "demonology", "destruction"],
    "monk": ["brewmaster", "mistweaver", "windwalker"],
    "druid": ["balance", "feral", "guardian", "restoration"],
    "demonhunter": ["havoc", "vengeance"],
    "evoker": ["augmentation", "devastation", "preservation"],
}


def get_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def leaderboard_url(wow_class: str, spec: str) -> str:
    return (
        f"{BASE_URL}/en-us/worldsoul/us/"
        f"leaderboards/pvp/shuffle/{wow_class}/{spec}"
    )


def parse_leaderboard(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    players = []

    for anchor in soup.find_all("a", href=True):
        armory_url = urljoin(BASE_URL, anchor["href"])
        if "/armory/character/" not in armory_url:
            continue

        name = anchor.get_text(" ", strip=True)
        if not name:
            continue

        players.append({"name": name, "armory_url": armory_url})

    unique = []
    seen = set()
    for player in players:
        key = (player["name"].casefold(), player["armory_url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(player)

    return unique[:TOP_PLAYERS_PER_SPEC]


def extract_profile_data(html: str) -> tuple[dict | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    state_script = soup.find("script", id=PROFILE_STATE_SCRIPT_ID)
    if state_script is None:
        return None, f"Missing script #{PROFILE_STATE_SCRIPT_ID}"

    script_text = state_script.string or state_script.get_text()
    json_start = script_text.find("{")
    if json_start == -1:
        return None, f"Script #{PROFILE_STATE_SCRIPT_ID} contains no JSON object"

    try:
        data, _ = json.JSONDecoder().raw_decode(script_text[json_start:])
    except json.JSONDecodeError as error:
        return None, f"Could not decode profile JSON: {error}"

    if not isinstance(data, dict) or not isinstance(data.get("character"), dict):
        return None, "Profile JSON does not contain a character object"

    return data, None


def profile_artifact_paths(
    armory_url: str, captured_at: float | None = None
) -> tuple[Path, Path]:
    path_parts = [part for part in urlsplit(armory_url).path.split("/") if part]
    try:
        character_index = path_parts.index("character")
        realm, character = path_parts[character_index + 1 : character_index + 3]
    except (ValueError, IndexError) as error:
        raise ValueError(f"Unexpected armory URL: {armory_url}") from error

    region = path_parts[character_index - 2] if character_index >= 2 else "unknown"

    def safe_component(value: str) -> str:
        component = quote(
            unquote(value),
            safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-",
        )
        reserved_names = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }
        if component in {".", ".."} or component.split(".")[0].upper() in reserved_names:
            return f"_{component}"
        return component

    directory = (
        OUTPUT_DIR
        / "captures"
        / safe_component(region)
        / safe_component(realm)
        / safe_component(character)
    )
    captured_at = captured_at if captured_at is not None else time.time()
    timestamp = datetime.fromtimestamp(captured_at, timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    return directory / f"{timestamp}.html", directory / f"{timestamp}.json"


def relative_archive_path(path: Path) -> str:
    return path.relative_to(OUTPUT_DIR).as_posix()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(path)


def write_json_atomic(path: Path, data: object) -> None:
    write_text_atomic(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_bytes(data)
    temporary_path.replace(path)


def image_urls(value: object) -> set[str]:
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found.update(image_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.update(image_urls(item))
    elif isinstance(value, str) and value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname in IMAGE_HOSTS
            and Path(parsed.path).suffix.lower() in IMAGE_EXTENSIONS
        ):
            found.add(value)
    return found


def fetch_profile_image(url: str) -> tuple[str, str | None, str | None]:
    parsed = urlsplit(url)
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest()
    extension = Path(parsed.path).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        extension = ".img"
    image_path = OUTPUT_DIR / "assets" / f"{filename}{extension}"
    if image_path.is_file():
        return url, relative_archive_path(image_path), None

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if not content_type.startswith("image/"):
            return url, None, f"Unexpected image content type: {content_type or 'missing'}"

        if extension == ".img":
            extension = mimetypes.guess_extension(content_type) or ".img"
        image_path = OUTPUT_DIR / "assets" / f"{filename}{extension}"

        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                return url, None, f"Image exceeds {MAX_IMAGE_BYTES} byte limit"
            chunks.append(chunk)

        if not chunks:
            return url, None, "Image response was empty"

        write_bytes_atomic(image_path, b"".join(chunks))
        return url, relative_archive_path(image_path), None
    except requests.RequestException as error:
        return url, None, str(error)


def archive_profile_images(
    profile_data: dict, profile_data_path: Path
) -> tuple[Path, dict]:
    urls = image_urls(profile_data)
    assets = {}
    errors = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_profile_image, url)
            for url in sorted(urls)
        ]
        for future in as_completed(futures):
            url, local_path, error = future.result()
            if local_path:
                assets[url] = local_path
            elif error:
                errors.append({"url": url, "error": error})
                print(f"  Could not archive image {url}: {error}")

    manifest_path = profile_data_path.with_name(
        profile_data_path.stem + ".assets.json"
    )
    write_json_atomic(
        manifest_path,
        {"assets": assets, "errors": errors},
    )
    return manifest_path, {"count": len(assets), "errors": errors}


def archive_existing_profile_images() -> None:
    for capture_root in ("captures", "armory"):
        root = OUTPUT_DIR / capture_root
        if not root.is_dir():
            continue
        for profile_path in root.rglob("*.json"):
            if profile_path.name.endswith(".assets.json"):
                continue
            try:
                profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                print(f"Skipping unreadable profile {profile_path}: {error}")
                continue
            if not isinstance(profile_data, dict) or not isinstance(
                profile_data.get("character"), dict
            ):
                continue

            print(f"Archiving images for {profile_path}")
            archive_profile_images(profile_data, profile_path)


def parse_armory_character(url: str) -> tuple[dict, dict | None]:
    captured_at = time.time()
    try:
        html = get_html(url)
    except requests.RequestException as error:
        print(f"  Failed to fetch {url}: {error}")
        return (
            {
                "armory_url": url,
                "captured": captured_at,
                "captured_at": datetime.fromtimestamp(
                    captured_at, timezone.utc
                ).isoformat(),
                "capture_status": "fetch_failed",
                "capture_error": str(error),
                "html_length": None,
                "raw_html_file": None,
                "profile_data_file": None,
            },
            None,
        )

    raw_path, data_path = profile_artifact_paths(url, captured_at)
    while raw_path.exists() or data_path.exists():
        captured_at += 0.000001
        raw_path, data_path = profile_artifact_paths(url, captured_at)
    write_text_atomic(raw_path, html)

    profile_data, parse_error = extract_profile_data(html)
    snapshot = {
        "armory_url": url,
        "captured": captured_at,
        "captured_at": datetime.fromtimestamp(
            captured_at, timezone.utc
        ).isoformat(),
        "capture_status": "captured" if profile_data is not None else "unparsed",
        "html_length": len(html),
        "raw_html_file": relative_archive_path(raw_path),
        "profile_data_file": None,
    }

    if profile_data is None:
        snapshot["capture_error"] = parse_error
        print(f"  Captured HTML but could not extract profile data: {parse_error}")
    else:
        write_json_atomic(data_path, profile_data)
        snapshot["profile_data_file"] = relative_archive_path(data_path)
        manifest_path, asset_result = archive_profile_images(
            profile_data, data_path
        )
        snapshot["local_assets_file"] = relative_archive_path(manifest_path)
        snapshot["local_asset_count"] = asset_result["count"]
        if asset_result["errors"]:
            snapshot["asset_errors"] = asset_result["errors"]

    return snapshot, profile_data


def load_cached_profile(record: dict) -> tuple[dict, dict] | None:
    snapshot = record.get("snapshot") or {}
    if snapshot.get("capture_status") != "captured":
        return None

    raw_path = OUTPUT_DIR / (snapshot.get("raw_html_file") or "")
    data_path = OUTPUT_DIR / (snapshot.get("profile_data_file") or "")
    if not raw_path.is_file() or not data_path.is_file():
        return None

    try:
        profile_data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"  Cached profile JSON is invalid; recapturing {record['character']['armory_url']}")
        return None

    if not isinstance(profile_data, dict) or not isinstance(
        profile_data.get("character"), dict
    ):
        print(f"  Cached profile data is incomplete; recapturing {record['character']['armory_url']}")
        return None

    return snapshot, profile_data


def spec_file_path(wow_class: str, spec: str) -> Path:
    return OUTPUT_DIR / wow_class / f"{spec}.json"


def write_all_builds() -> None:
    all_builds = []
    for wow_class, specs in CLASSES.items():
        for spec in specs:
            path = spec_file_path(wow_class, spec)
            if path.is_file():
                all_builds.extend(json.loads(path.read_text(encoding="utf-8")))
    write_json_atomic(OUTPUT_DIR / "all_builds.json", all_builds)


def record_capture(build: dict) -> None:
    snapshot = build.get("snapshot") or {}
    profile_data_file = snapshot.get("profile_data_file")
    if snapshot.get("capture_status") != "captured" or not profile_data_file:
        return

    index_path = OUTPUT_DIR / "capture_index.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {"schema_version": 1, "captures": []}
    )
    appearance = {"class": build["class"], "spec": build["spec"]}
    capture = next(
        (
            item
            for item in index["captures"]
            if item.get("snapshot", {}).get("profile_data_file")
            == profile_data_file
        ),
        None,
    )

    if capture is None:
        index["captures"].append(
            {
                "class": build["class"],
                "spec": build["spec"],
                "character": build["character"],
                "snapshot": snapshot,
                "appearances": [appearance],
            }
        )
    else:
        appearances = capture.setdefault("appearances", [])
        if appearance not in appearances:
            appearances.append(appearance)

    write_json_atomic(index_path, index)


def scrape_spec(wow_class: str, spec: str, refresh: bool = False) -> list[dict]:
    url = leaderboard_url(wow_class, spec)
    print(f"Scraping {wow_class}/{spec}")

    top_players = parse_leaderboard(get_html(url))
    spec_path = spec_file_path(wow_class, spec)
    previous_builds = (
        json.loads(spec_path.read_text(encoding="utf-8"))
        if spec_path.is_file()
        else []
    )
    builds = []

    for player in top_players:
        record = (
            next(
                (
                    build
                    for build in previous_builds
                    if build.get("character", {}).get("armory_url")
                    == player["armory_url"]
                ),
                None,
            )
            if not refresh
            else None
        )

        cached = load_cached_profile(record) if record is not None else None
        if cached is None:
            print(f"  {player['armory_url']}")
            snapshot, profile_data = parse_armory_character(player["armory_url"])
        else:
            snapshot, profile_data = cached
            print(f"  Reusing captured profile: {player['armory_url']}")

        character = dict(player)
        if profile_data is not None:
            character["name"] = profile_data["character"].get(
                "name", character["name"]
            )

        builds.append(
            {
                "class": wow_class,
                "spec": spec,
                "character": character,
                "snapshot": snapshot,
            }
        )

        record_capture(builds[-1])
        write_json_atomic(spec_path, builds)
        write_all_builds()
        time.sleep(REQUEST_DELAY_SECONDS)

    if not top_players:
        write_json_atomic(spec_path, builds)
        write_all_builds()

    return builds


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive current top solo-shuffle armory profiles."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recapture profiles even when a complete local capture already exists.",
    )
    parser.add_argument(
        "--download-assets",
        action="store_true",
        help="Download images referenced by existing profile JSON, then exit.",
    )
    args = parser.parse_args()

    if args.download_assets:
        archive_existing_profile_images()
        return

    for wow_class, specs in CLASSES.items():
        (OUTPUT_DIR / wow_class).mkdir(parents=True, exist_ok=True)
        for spec in specs:
            try:
                scrape_spec(wow_class, spec, refresh=args.refresh)
            except requests.RequestException as error:
                print(f"FAILED {wow_class}/{spec}: {error}")

    print("Done.")


if __name__ == "__main__":
    main()
