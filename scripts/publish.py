"""Publish a SideStore source from the official OpenTubeX release IPAs."""

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

UPSTREAM = "OpenTubeX/OpenTubeX"
REPOSITORY = "OpenTubeX/sidestore"
SOURCE_URL = "https://sidestore.opentubex.org/source.json"
NIGHTLY_ID = "org.opentubex.app.nightly"
ROOT = Path(__file__).resolve().parents[1]


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def select_releases(releases):
    selected = {}
    for release in sorted(releases, key=lambda item: item["published_at"] or "", reverse=True):
        if release["draft"]:
            continue
        tag = release["tag_name"]
        if re.fullmatch(r"v\d+\.\d+\.\d+-nightly-\d+", tag):
            channel = "nightly"
        elif re.fullmatch(r"v\d+\.\d+\.\d+(?:-beta)?", tag) and not release["prerelease"]:
            channel = "stable"
        else:
            continue
        assets = [asset for asset in release["assets"] if asset["name"].endswith("-ios-unsigned.ipa")]
        if channel not in selected and len(assets) == 1:
            selected[channel] = (release, assets[0])
    if set(selected) != {"stable", "nightly"}:
        raise ValueError("Both stable and nightly releases with an unsigned IPA are required")
    return selected


def inspect_ipa(path):
    with zipfile.ZipFile(path) as archive:
        plists = [name for name in archive.namelist()
                  if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
        if len(plists) != 1:
            raise ValueError("Expected exactly one app in the IPA")
        plist_path = plists[0]
        info = plistlib.loads(archive.read(plist_path))
        for key in ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion", "MinimumOSVersion"):
            if not isinstance(info.get(key), str) or not info[key]:
                raise ValueError(f"Missing IPA metadata: {key}")
        return plist_path, info


def repackage_nightly(original, output):
    plist_path, info = inspect_ipa(original)
    if info["CFBundleIdentifier"] != "org.opentubex.app":
        raise ValueError("Expected the official unsigned OpenTubeX app identity")
    app_path = plist_path.removesuffix("Info.plist")
    info["CFBundleIdentifier"] = NIGHTLY_ID
    info["CFBundleDisplayName"] = "OpenTubeX Nightly"
    info["CFBundleName"] = "OpenTubeX Nightly"
    for url_type in info.get("CFBundleURLTypes", []):
        if url_type.get("CFBundleURLName") == "org.opentubex.app":
            url_type["CFBundleURLName"] = NIGHTLY_ID

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(output, "w") as target:
        names = source.namelist()
        if any(name.startswith(app_path + "_CodeSignature/") or
               name == app_path + "embedded.mobileprovision" or
               name.startswith(app_path + "PlugIns/") for name in names):
            raise ValueError("Nightly repackaging requires an unsigned app without extensions")
        config_path = app_path + "capacitor.config.json"
        if config_path not in names:
            raise ValueError("Missing Capacitor configuration")
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename == plist_path:
                content = plistlib.dumps(info, fmt=plistlib.FMT_BINARY)
            elif entry.filename == config_path:
                config = json.loads(content)
                config.update(appId=NIGHTLY_ID, appName="OpenTubeX Nightly")
                content = (json.dumps(config, indent=2) + "\n").encode()
            # Retain file permissions, symlinks, timestamps and nested frameworks.
            target.writestr(entry, content)
    return info


def app_listing(channel, release, ipa, download_url):
    _, info = inspect_ipa(ipa)
    expected_id = NIGHTLY_ID if channel == "nightly" else "org.opentubex.app"
    if info["CFBundleIdentifier"] != expected_id:
        raise ValueError(f"Unexpected {channel} IPA identity")
    privacy = {key: value for key, value in info.items() if key.startswith("NS") and key.endswith("UsageDescription")}
    with ipa.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    return {
        "name": "OpenTubeX Nightly" if channel == "nightly" else "OpenTubeX",
        "bundleIdentifier": expected_id,
        "developerName": "OpenTubeX",
        "subtitle": "Latest development builds" if channel == "nightly" else "Stable beta releases",
        "localizedDescription": "A customizable, privacy-focused YouTube client for iPhone and iPad. "
                                + ("Nightly builds follow development and install alongside the stable app."
                                   if channel == "nightly" else "The stable channel contains official beta releases."),
        "iconURL": f"https://sidestore.opentubex.org/{channel}.png",
        "tintColor": "2EC4B6",
        "appPermissions": {"entitlements": [], "privacy": privacy},
        "versions": [{
            "version": info["CFBundleShortVersionString"],
            "buildVersion": info["CFBundleVersion"],
            "date": release["published_at"],
            "localizedDescription": f"{release['tag_name']}\n\nRelease notes: {release['html_url']}",
            "downloadURL": download_url,
            "size": ipa.stat().st_size,
            "sha256": digest,
            "minOSVersion": info["MinimumOSVersion"],
        }],
    }


def publish_nightly(release, ipa):
    tag = release["tag_name"]
    # Query first so an unchanged scheduled run never replaces an installed build.
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    existing = next((item for page in releases for item in page if item["tag_name"] == tag), None)
    if existing:
        asset = next((item for item in existing["assets"] if item["name"] == ipa.name), None)
        if asset:
            with ipa.open("rb") as file:
                digest = "sha256:" + hashlib.file_digest(file, "sha256").hexdigest()
            if asset.get("digest") == digest:
                return asset["browser_download_url"]
            # Older GitHub release assets may not expose a digest.
            with tempfile.TemporaryDirectory() as directory:
                gh("release", "download", tag, "--repo", REPOSITORY,
                   "--pattern", ipa.name, "--dir", directory)
                if Path(directory, ipa.name).read_bytes() != ipa.read_bytes():
                    raise ValueError("Published nightly differs from this package; refusing to replace it")
            return asset["browser_download_url"]
        gh("release", "upload", tag, str(ipa), "--repo", REPOSITORY)
    else:
        notes = (f"OpenTubeX Nightly for SideStore, based on [{tag}]({release['html_url']}).\n\n"
                 "The unsigned IPA has a separate bundle identifier and display name so it can "
                 "install alongside stable. SideStore signs it with your Apple Account.\n\n"
                 f"App source: https://github.com/{UPSTREAM}/tree/{tag}\n"
                 "Repository source: https://github.com/OpenTubeX/sidestore\n")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as file:
            file.write(notes)
            file.flush()
            gh("release", "create", tag, str(ipa), "--repo", REPOSITORY,
               "--title", f"OpenTubeX Nightly {tag.removeprefix('v')}",
               "--notes-file", file.name, "--prerelease", "--latest=false")
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ipa.name}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Upload nightly IPA to GitHub Releases")
    args = parser.parse_args()
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{UPSTREAM}/releases?per_page=100"))
    selected = select_releases([release for page in pages for release in page])
    incoming = ROOT / "incoming"
    incoming.mkdir(exist_ok=True)
    apps = []
    for channel in ("stable", "nightly"):
        release, asset = selected[channel]
        directory = incoming / release["tag_name"]
        directory.mkdir(exist_ok=True)
        gh("release", "download", release["tag_name"], "--repo", UPSTREAM,
           "--pattern", asset["name"], "--dir", str(directory), "--clobber")
        ipa = directory / asset["name"]
        if ipa.stat().st_size != asset["size"]:
            raise ValueError("Downloaded IPA size does not match release metadata")
        download_url = asset["browser_download_url"]
        if channel == "nightly" and inspect_ipa(ipa)[1]["CFBundleIdentifier"] != NIGHTLY_ID:
            output = directory / asset["name"].replace("-ios-unsigned.ipa", "-ios-nightly.ipa")
            repackage_nightly(ipa, output)
            ipa = output
            download_url = (publish_nightly(release, ipa) if args.publish else
                            f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{ipa.name}")
        apps.append(app_listing(channel, release, ipa, download_url))
        print(f"{channel}: {release['tag_name']} ({apps[-1]['bundleIdentifier']})")
    site = ROOT / "site"
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ROOT / "static", site)
    source = {"name": "OpenTubeX", "identifier": "org.opentubex.sidestore",
              "sourceURL": SOURCE_URL, "apps": apps, "news": []}
    (site / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    for channel, app in zip(("stable", "nightly"), apps):
        channel_source = {**source, "identifier": f"org.opentubex.sidestore.{channel}",
                          "name": f"OpenTubeX {channel.title()}", "apps": [app],
                          "sourceURL": SOURCE_URL.replace("source.json", f"{channel}.json")}
        (site / f"{channel}.json").write_text(json.dumps(channel_source, indent=2) + "\n")


if __name__ == "__main__":
    main()
