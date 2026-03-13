# Ninebot Integration for Home Assistant

A custom integration to connect Ninebot cloud devices into Home Assistant.

## Requirements

- Home Assistant Core >= 2024.4.0

## Install via HACS

1. Open HACS -> Integrations -> menu (three dots) -> Custom repositories.
2. Add repository URL: `https://github.com/Wuty-zju/ha_ninebot`
3. Category: `Integration`
4. Search `Ninebot` in HACS and click `Download`.
5. Restart Home Assistant.
6. Go to Settings -> Devices & Services -> Add Integration -> `Ninebot`.

## Update via HACS (versioned releases)

HACS checks GitHub releases/tags as versions.

Recommended release flow:

1. Bump `version` in `custom_components/ninebot/manifest.json`.
2. Commit and push to `main`.
3. Create and push a git tag (same as manifest version), for example:

```bash
git tag 0.1.1
git push origin 0.1.1
```

4. (Optional but recommended) create a GitHub Release for that tag.

After that, HACS will detect a new version and offer update.

## Development Notes

- Integration code is under `custom_components/ninebot`.
- HACS metadata is in `hacs.json`.
