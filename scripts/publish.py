"""Publish a SideStore source from the official OpenTubeX release IPAs."""

import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

UPSTREAM = "OpenTubeX/OpenTubeX"
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


def app_listing(channel, release, ipa, download_url):
    _, info = inspect_ipa(ipa)
    expected_id = NIGHTLY_ID if channel == "nightly" else "org.opentubex.app"
    if info["CFBundleIdentifier"] != expected_id:
        raise ValueError(f"Unexpected {channel} IPA identity: expected {expected_id}, "
                         f"got {info['CFBundleIdentifier']}")
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


def main():
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
