# grokbot-exporter

Export Grok Bot chats, profiles, skills, routines, and files to a private folder.

## Install

Requires Linux, Python 3.11+, Node.js 22+, `playwright-cli`, and a signed-in
Grok Bot app. The reader was checked with Grok Bot 0.70.2 and Playwright CLI 0.1.19.

```sh
git clone https://github.com/StefMVP/grokbot-exporter.git
cd grokbot-exporter
python -m venv .venv
. .venv/bin/activate
pip install .
npm install -g @playwright/cli@0.1.19
```

## Export

```sh
grokbot-exporter launch-source --app '/opt/Grok Bot/grok-bot' \
  --profile "$HOME/.config/Grok Bot" --session grok-export \
  --output "$HOME/grok-export-private/browser"
grokbot-exporter capture --session grok-export --output "$HOME/grok-export-private/export"
grokbot-exporter check --source "$HOME/grok-export-private/export"
```

This uses a separate profile copy. It saves raw JSON and a manifest with file
checksums and capture errors. Keep the folder private. The browser copy contains
sign-in credentials. Capture does not submit prompts or change source schedules.

For cloud files, run [cloud_export.py](grokbot_exporter/cloud_export.py) in the
source cloud terminal. Use `pull-cloud` and `recover-files` before stopping the
source process:

```sh
grokbot-exporter stop-source --output "$HOME/grok-export-private/browser"
```

## Adapters

- [OpenMaus](grokbot_exporter/adapters/openmaus/README.md): Convert, import, and verify a saved export.
- TODO: Add adapters for other tools.

## Check the tool

The digital twin tests use fictional data and local services.

```sh
grokbot-exporter --help
python -m unittest discover -s tests -v
```

[MIT license](LICENSE)
