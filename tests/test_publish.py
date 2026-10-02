import importlib.util
import hashlib
import json
import plistlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("publish", Path(__file__).resolve().parents[1] / "scripts/publish.py")
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


def release(tag, date, *, ipa=True, prerelease=False, draft=False):
    return {"tag_name": tag, "published_at": date, "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/OpenTubeX/OpenTubeX/releases/tag/{tag}",
            "assets": [{"name": f"opentubex-{tag}-ios-unsigned.ipa"}] if ipa else []}


class PublishTests(unittest.TestCase):
    def test_selects_newest_completed_channels_by_publication_time(self):
        stable = release("v0.35.2-beta", "2026-09-30T22:00:00Z")
        nightly = release("v0.35.2-nightly-1757", "2026-10-02T02:00:00Z", prerelease=True)
        releases = [
            release("v0.35.3-nightly-1758", "2026-10-03T02:00:00Z", ipa=False, prerelease=True),
            release("v0.35.3-beta", "2026-10-03T02:00:00Z", draft=True),
            release("v0.35.3-RC-1", "2026-10-03T02:00:00Z", prerelease=True),
            nightly, stable,
            release("v0.35.1-beta", "2026-09-20T22:00:00Z"),
        ]
        selected = publish.select_releases(releases)
        self.assertEqual(selected["stable"][0], stable)
        self.assertEqual(selected["nightly"][0], nightly)

    def test_missing_channel_fails_instead_of_publishing_an_incomplete_source(self):
        with self.assertRaises(ValueError):
            publish.select_releases([release("v0.35.2-beta", "2026-10-01T00:00:00Z")])

    def make_ipa(self, directory, *, channel="stable", bundle_id=None):
        path = Path(directory, f"{channel}.ipa")
        app_id = publish.NIGHTLY_ID if channel == "nightly" else "org.opentubex.app"
        info = {"CFBundleIdentifier": bundle_id or app_id,
                "CFBundleVersion": "13499", "CFBundleShortVersionString": "0.35.2",
                "MinimumOSVersion": "17.4", "NSCameraUsageDescription": "Scan a QR code."}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Payload/App.app/Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
        return path

    def test_listing_matches_actual_ipa_including_build_and_permissions(self):
        for channel in ("stable", "nightly"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                ipa = self.make_ipa(directory, channel=channel)
                tag = "v0.35.2-nightly-1758" if channel == "nightly" else "v0.35.2-beta"
                metadata = release(tag, "2026-10-02T02:00:00Z", prerelease=channel == "nightly")
                url = f"https://github.com/OpenTubeX/OpenTubeX/releases/download/{tag}/{ipa.name}"
                listing = publish.app_listing(channel, metadata, ipa, url)
                expected_id = publish.NIGHTLY_ID if channel == "nightly" else "org.opentubex.app"
                self.assertEqual(listing["bundleIdentifier"], expected_id)
                version = listing["versions"][0]
                self.assertEqual(version["version"], "0.35.2")
                self.assertEqual(version["buildVersion"], "13499")
                self.assertEqual(version["downloadURL"], url)
                self.assertEqual(version["size"], ipa.stat().st_size)
                self.assertEqual(version["sha256"], hashlib.sha256(ipa.read_bytes()).hexdigest())
                self.assertEqual(version["minOSVersion"], "17.4")
                self.assertEqual(listing["appPermissions"]["privacy"], {"NSCameraUsageDescription": "Scan a QR code."})
                other_channel = "stable" if channel == "nightly" else "nightly"
                with self.assertRaisesRegex(ValueError, f"Unexpected {other_channel} IPA identity"):
                    publish.app_listing(other_channel, metadata, ipa, url)

    def run_publisher(self, root, *, nightly_identity="nightly"):
        fixtures = root / "fixtures"
        fixtures.mkdir()
        releases = [release("v0.35.2-beta", "2026-09-30T22:00:00Z"),
                    release("v0.35.2-nightly-1758", "2026-10-02T12:00:00Z", prerelease=True)]
        ipas = {}
        for channel, metadata in zip(("stable", "nightly"), releases):
            fixture = fixtures / channel
            fixture.mkdir()
            ipa = self.make_ipa(fixture, channel=nightly_identity if channel == "nightly" else channel)
            asset = metadata["assets"][0]
            asset.update(size=ipa.stat().st_size,
                         browser_download_url=f"https://github.com/OpenTubeX/OpenTubeX/releases/download/{metadata['tag_name']}/{asset['name']}")
            ipas[metadata["tag_name"]] = ipa

        def gh(*args):
            if args == ("api", "--paginate", "--slurp", "repos/OpenTubeX/OpenTubeX/releases?per_page=100"):
                return json.dumps([releases])
            if args[:2] == ("release", "download") and args[3:5] == ("--repo", "OpenTubeX/OpenTubeX"):
                shutil.copyfile(ipas[args[2]], Path(args[8], args[6]))
                return ""
            self.fail(f"Unexpected GitHub operation: {args}")

        static = root / "static"
        static.mkdir()
        (static / "index.html").write_text("SideStore")
        with patch.object(publish, "ROOT", root), patch.object(publish, "gh", side_effect=gh):
            ready = publish.main()
        return releases, ipas, ready

    def test_unexpected_nightly_identity_is_not_treated_as_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = self.make_ipa(directory, channel="nightly", bundle_id="org.unexpected.app")
            metadata = release("v0.35.2-nightly-1758", "2026-10-02T12:00:00Z", prerelease=True)
            with self.assertRaisesRegex(ValueError, "Unexpected nightly IPA identity") as error:
                publish.app_listing("nightly", metadata, ipa, "https://example.org/app.ipa")
            self.assertNotIsInstance(error.exception, publish.NightlyNotReady)

    def test_generates_all_sources_using_unmodified_official_release_ipas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases, ipas, ready = self.run_publisher(root)
            self.assertTrue(ready)
            source = json.loads((root / "site/source.json").read_text())
            self.assertEqual(len(source["apps"]), 2)
            for channel, metadata, app in zip(("stable", "nightly"), releases, source["apps"]):
                asset = metadata["assets"][0]
                self.assertEqual(app["versions"][0]["downloadURL"], asset["browser_download_url"])
                self.assertEqual(app["versions"][0]["buildVersion"], "13499")
                downloaded = root / "incoming" / metadata["tag_name"] / asset["name"]
                self.assertEqual(downloaded.read_bytes(), ipas[metadata["tag_name"]].read_bytes())
                channel_source = json.loads((root / "site" / f"{channel}.json").read_text())
                self.assertEqual(channel_source["apps"], [app])
                self.assertEqual(channel_source["sourceURL"], f"https://sidestore.opentubex.org/{channel}.json")

    def test_old_nightly_identity_leaves_existing_site_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            site.mkdir()
            existing = site / "source.json"
            existing.write_text("existing source")
            with patch("builtins.print") as output:
                _, _, ready = self.run_publisher(root, nightly_identity="stable")
            self.assertFalse(ready)
            self.assertTrue(any("::notice" in call.args[0] and "Waiting" in call.args[0]
                                for call in output.call_args_list))
            self.assertEqual(existing.read_text(), "existing source")


if __name__ == "__main__":
    unittest.main()
