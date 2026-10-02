import importlib.util
import json
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

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

    def make_ipa(self, directory, *, signed=False, extension=False):
        path = Path(directory, "original.ipa")
        info = {"CFBundleIdentifier": "org.opentubex.app", "CFBundleDisplayName": "OpenTubeX",
                "CFBundleVersion": "13499", "CFBundleShortVersionString": "0.35.2",
                "MinimumOSVersion": "17.4", "NSCameraUsageDescription": "Scan a QR code.",
                "CFBundleURLTypes": [{"CFBundleURLName": "org.opentubex.app", "CFBundleURLSchemes": ["opentubex"]}]}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Payload/App.app/Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
            archive.writestr("Payload/App.app/capacitor.config.json", json.dumps({"appId": "org.opentubex.app", "appName": "OpenTubeX", "plugins": {"Share": {}}}))
            executable = zipfile.ZipInfo("Payload/App.app/App")
            executable.external_attr = 0o100755 << 16
            archive.writestr(executable, b"executable")
            archive.writestr("Payload/App.app/Frameworks/Python.framework/_CodeSignature/CodeResources", b"framework-signature")
            if signed:
                archive.writestr("Payload/App.app/_CodeSignature/CodeResources", b"app-signature")
            if extension:
                archive.writestr("Payload/App.app/PlugIns/Extension.appex/Info.plist", b"extension")
        return path

    def test_nightly_identity_changes_without_touching_executable_or_frameworks(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_ipa(directory)
            output = Path(directory, "nightly.ipa")
            publish.repackage_nightly(original, output)
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(output) as target:
                self.assertEqual(source.namelist(), target.namelist())
                for name in source.namelist():
                    if name.endswith(("Info.plist", "capacitor.config.json")):
                        continue
                    self.assertEqual(source.read(name), target.read(name))
                    self.assertEqual(source.getinfo(name).external_attr, target.getinfo(name).external_attr)
                info = plistlib.loads(target.read("Payload/App.app/Info.plist"))
                self.assertEqual(info["CFBundleIdentifier"], publish.NIGHTLY_ID)
                self.assertEqual(info["CFBundleDisplayName"], "OpenTubeX Nightly")
                self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["opentubex"])
                config = json.loads(target.read("Payload/App.app/capacitor.config.json"))
                self.assertEqual(config["appId"], publish.NIGHTLY_ID)
                self.assertEqual(config["appName"], "OpenTubeX Nightly")
                self.assertEqual(config["plugins"], {"Share": {}})
            second = Path(directory, "second.ipa")
            publish.repackage_nightly(original, second)
            self.assertEqual(output.read_bytes(), second.read_bytes())
            self.assertEqual(publish.inspect_ipa(original)[1]["CFBundleIdentifier"], "org.opentubex.app")

    def test_refuses_to_repackage_signed_apps_or_extensions(self):
        for flags in ({"signed": True}, {"extension": True}):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as directory:
                original = self.make_ipa(directory, **flags)
                with self.assertRaises(ValueError):
                    publish.repackage_nightly(original, Path(directory, "nightly.ipa"))

    def test_listing_matches_actual_ipa_including_build_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_ipa(directory)
            output = Path(directory, "nightly.ipa")
            publish.repackage_nightly(original, output)
            metadata = release("v0.35.2-nightly-1757", "2026-10-02T02:00:00Z", prerelease=True)
            listing = publish.app_listing("nightly", metadata, output, "https://example.org/nightly.ipa")
            self.assertEqual(listing["bundleIdentifier"], publish.NIGHTLY_ID)
            version = listing["versions"][0]
            self.assertEqual(version["version"], "0.35.2")
            self.assertEqual(version["buildVersion"], "13499")
            self.assertEqual(version["size"], output.stat().st_size)
            self.assertEqual(version["minOSVersion"], "17.4")
            self.assertEqual(listing["appPermissions"]["privacy"], {"NSCameraUsageDescription": "Scan a QR code."})
            with self.assertRaises(ValueError):
                publish.app_listing("stable", metadata, output, "https://example.org/nightly.ipa")


if __name__ == "__main__":
    unittest.main()
