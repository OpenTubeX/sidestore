# OpenTubeX SideStore repository

This repository publishes the official iPhone and iPad app source at
[sidestore.opentubex.org](https://sidestore.opentubex.org). It contains the
stable beta app and a separately installable nightly app, each with its own
settings and library.

## Add the repository

Set up [SideStore](https://docs.sidestore.io/docs/installation/install), then
scan the QR code or open **Browse → Sources → +** in SideStore and add this URL:

<p align="center">
  <img src="static/repository-qr.svg" width="320" height="320" alt="QR code to add the OpenTubeX source to SideStore">
</p>

```text
https://sidestore.opentubex.org/source.json
```

The main source includes both apps. To see only one channel, use
[stable.json](https://sidestore.opentubex.org/stable.json) or
[nightly.json](https://sidestore.opentubex.org/nightly.json) instead.

The app requires iOS or iPadOS 17.4 or newer. SideStore signs the unsigned IPA
with your Apple Account. See the
[iOS installation guide](https://opentubex.org/docs/installing/#ios--ipados-experimental)
for device setup and signing requirements.

## How publishing works

The publish workflow downloads the unsigned iOS IPAs for the latest stable and
nightly releases in `OpenTubeX/OpenTubeX`. It reads their app identity, version,
build number, minimum OS version, and privacy permission descriptions, then
generates the combined source and both channel-only sources. Each source links
directly to the original IPA in the application repository's GitHub release.

Stable uses `org.opentubex.app`; nightly uses `org.opentubex.app.nightly` so both
can be installed on the same device. Publishing fails if either channel is
missing or an IPA has the wrong identity, leaving the deployed source intact.
The workflow does not modify IPAs or create GitHub releases.

The sources and landing page are deployed to GitHub Pages. The workflow runs on
pushes to `main`, every 30 minutes, manual runs, and `opentubex-release` or
`opentubex-nightly` repository dispatch events.

The catalog icons live in `static/stable.png` and `static/nightly.png`. Keep
both PNGs in sync with the app's branding, including the nightly wrench badge.

## Maintainer setup

Configure GitHub Pages to use GitHub Actions, set `sidestore.opentubex.org` as
the custom domain, and add a DNS `CNAME` from that name to `opentubex.github.io`.
The workflow uses the automatic `GITHUB_TOKEN` to read releases and deploy
Pages; no Apple signing credentials or additional Actions secrets are required.
Any external release dispatch must use a token with permission to send
repository dispatch events to this repository.

For local checks, install Python 3.11 or newer and GitHub CLI, authenticate
with `gh auth login`, then run:

```sh
python -m unittest discover -s tests -v
python scripts/publish.py
```

Downloaded IPAs go into `incoming/`; the generated site goes into `site/`.
Both directories are ignored by Git.
