import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

REGION_HOSTS = {
    "us": "us.api.blizzard.com",
    "eu": "eu.api.blizzard.com",
    "kr": "kr.api.blizzard.com",
    "tw": "tw.api.blizzard.com",
}
OAUTH_URL = "https://oauth.battle.net/token"
REQUEST_TIMEOUT_SECONDS = 30


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def safe_path_slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError(f"Unexpected class/spec slug from snapshot: {value!r}")
    return value


def read_snapshot_targets(archive_dir: Path) -> dict[int, dict]:
    targets = {}
    capture_root = archive_dir / "captures"
    for profile_path in capture_root.rglob("*.json"):
        if profile_path.name.endswith(".assets.json"):
            continue
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"Skipping unreadable profile {profile_path}: {error}")
            continue

        character = profile.get("character") if isinstance(profile, dict) else None
        class_info = (character or {}).get("class") or {}
        class_id = class_info.get("id")
        if not isinstance(class_id, int):
            continue

        target = targets.setdefault(
            class_id,
            {
                "id": class_id,
                "name": class_info.get("name", ""),
                "slug": safe_path_slug(
                    class_info.get("slug") or slug(class_info.get("name", ""))
                ),
                "specs": {},
            },
        )
        for specialization in (character.get("specs") or []):
            spec_name = specialization.get("name")
            spec_slug = safe_path_slug(
                specialization.get("id") or slug(spec_name or "")
            )
            if not spec_name or not spec_slug:
                continue
            target["specs"][slug(spec_name)] = {
                "slug": spec_slug,
                "name": spec_name,
            }

    return targets


class BlizzardGameDataClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        region: str,
        namespace: str,
        locale: str,
    ) -> None:
        self.host = REGION_HOSTS[region]
        self.namespace = namespace
        self.locale = locale
        self.session = requests.Session()
        token_response = self.session.post(
            OAUTH_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise RuntimeError("Blizzard OAuth response did not include an access token")
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    def get(self, path: str) -> dict:
        if path.startswith(("https://", "http://")):
            parsed = urlsplit(path)
            if parsed.scheme != "https" or parsed.hostname != self.host:
                raise ValueError(f"Unexpected Blizzard API link host: {parsed.hostname}")
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            api_path = parsed.path
        else:
            api_path = path
            query = {}

        query.setdefault("namespace", self.namespace)
        query.setdefault("locale", self.locale)
        response = self.session.get(
            f"https://{self.host}{api_path}",
            params=query,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError(f"Expected a JSON object from {api_path}")
        return result


def reference_url(reference: dict) -> str:
    key = reference.get("key") or {}
    href = key.get("href")
    if not href:
        raise ValueError(f"Blizzard API reference has no key.href: {reference}")
    return href


def specialization_from_reference(reference: dict) -> dict:
    specialization = reference.get("playable_specialization")
    if isinstance(specialization, dict):
        return specialization
    specialization = reference.get("specialization")
    if isinstance(specialization, dict):
        return specialization
    return reference


def specialization_tree_url(
    class_tree: dict, reference: dict, specialization: dict
) -> str:
    href = None
    try:
        href = reference_url(reference)
    except ValueError:
        try:
            href = reference_url(specialization)
        except ValueError:
            pass

    if href and re.search(
        r"/talent-tree/\d+/playable-specialization/\d+",
        urlsplit(href).path,
    ):
        return href

    tree_id = class_tree.get("id")
    specialization_id = specialization.get("id")
    if tree_id is not None and specialization_id is not None:
        return (
            f"/data/wow/talent-tree/{tree_id}"
            f"/playable-specialization/{specialization_id}"
        )
    if href:
        raise ValueError(
            "Blizzard specialization reference does not link to a talent "
            f"tree specialization: {href}"
        )
    raise ValueError(
        "Blizzard specialization reference is missing both a talent-tree "
        "URL and the IDs needed to construct one"
    )


def read_layouts(
    archive_dir: Path,
    client: BlizzardGameDataClient,
    targets: dict[int, dict],
    region: str,
    namespace: str,
    locale: str,
) -> dict:
    if not targets:
        raise RuntimeError("No character snapshots with class/spec data were found")

    tree_index = client.get("/data/wow/talent-tree/index")
    spec_references = tree_index.get("spec_talent_trees") or []
    if not spec_references:
        raise ValueError("Talent tree index did not contain spec_talent_trees")

    saved_layouts = []
    api_class_ids = set()
    api_spec_names = set()
    for spec_reference in spec_references:
        specialization_reference = specialization_from_reference(spec_reference)
        spec_url = specialization_tree_url(
            {}, spec_reference, specialization_reference
        )
        spec_tree = client.get(spec_url)
        playable_class = spec_tree.get("playable_class") or {}
        specialization = spec_tree.get("playable_specialization") or {}
        playable_class_id = playable_class.get("id")
        spec_name = specialization.get("name")
        if playable_class_id is not None:
            api_class_ids.add(playable_class_id)
        if spec_name:
            api_spec_names.add(spec_name)

        class_target = targets.get(playable_class_id)
        if class_target is None:
            continue
        spec_target = class_target["specs"].get(slug(spec_name or ""))
        if spec_target is None:
            continue

        class_slug = class_target["slug"]
        spec_slug = spec_target["slug"]
        relative_path = (
            Path("talent_layouts")
            / namespace
            / class_slug
            / f"{spec_slug}.json"
        )
        layout_path = archive_dir / relative_path
        layout_record = {
            "schema_version": 1,
            "source": "Blizzard Game Data API",
            "source_url": spec_url,
            "region": region,
            "namespace": namespace,
            "locale": locale,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "class": {
                "id": playable_class_id,
                "name": playable_class.get("name"),
                "slug": class_slug,
            },
            "specialization": {
                "id": specialization.get("id"),
                "name": spec_name,
                "slug": spec_slug,
            },
            "tree": spec_tree,
        }
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.write_text(
            json.dumps(layout_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        saved_layouts.append(
            {
                "class": class_slug,
                "spec": spec_slug,
                "path": relative_path.as_posix(),
                "tree_id": spec_tree.get("id"),
                "specialization_id": specialization.get("id"),
            }
        )
        print(f"Saved {class_target['name']} / {spec_target['name']}")

    expected_count = sum(len(target["specs"]) for target in targets.values())
    if not saved_layouts:
        details = (
            f"Snapshot class IDs: {', '.join(str(class_id) for class_id in targets)}; "
            f"API class IDs: {', '.join(str(class_id) for class_id in sorted(api_class_ids)) or 'none'}; "
            f"API specializations: {', '.join(sorted(api_spec_names)) or 'none'}"
        )
        raise RuntimeError(
            "No specialization layouts matched the archived snapshots. "
            + details
        )

    index = {
        "schema_version": 1,
        "source": "Blizzard Game Data API",
        "region": region,
        "namespace": namespace,
        "locale": locale,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "expected_layout_count": expected_count,
        "layout_count": len(saved_layouts),
        "layouts": saved_layouts,
    }
    index_path = archive_dir / "talent_layouts" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download patch-specific Blizzard talent tree geometry for the "
            "existing character snapshots. Character snapshots are read-only."
        )
    )
    parser.add_argument("--archive-dir", type=Path, default=Path("wow_archive"))
    parser.add_argument("--region", choices=REGION_HOSTS, default="us")
    parser.add_argument(
        "--namespace",
        help="Static API namespace. Defaults to static-<region>.",
    )
    parser.add_argument("--locale", default="en_US")
    args = parser.parse_args()

    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error(
            "Set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET for a Blizzard "
            "Developer Portal application. Credentials are read from the "
            "environment and are not written to the archive."
        )

    archive_dir = args.archive_dir.resolve()
    if not archive_dir.is_dir():
        parser.error(f"Archive directory does not exist: {archive_dir}")

    namespace = args.namespace or f"static-{args.region}"
    if not re.fullmatch(r"static-[A-Za-z0-9._-]+", namespace):
        parser.error(f"Invalid static API namespace: {namespace!r}")
    targets = read_snapshot_targets(archive_dir)
    try:
        client = BlizzardGameDataClient(
            client_id,
            client_secret,
            args.region,
            namespace,
            args.locale,
        )
        index = read_layouts(
            archive_dir,
            client,
            targets,
            args.region,
            namespace,
            args.locale,
        )
    except (requests.RequestException, ValueError, RuntimeError) as error:
        parser.error(f"Could not download talent tree layouts: {error}")

    print(
        f"Saved {index['layout_count']} layouts "
        f"({index['expected_layout_count']} requested) to "
        f"{archive_dir / 'talent_layouts'}"
    )


if __name__ == "__main__":
    main()
