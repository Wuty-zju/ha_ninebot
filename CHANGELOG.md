# Changelog

## v0.5.1

- Force-fixed entity naming and registration to deterministic IDs based on SN:
  - sensor.ninebot_[SN]_[key]
  - binary_sensor.ninebot_[SN]_[key]
  - image.ninebot_[SN]_[key]
  - lock.ninebot_[SN]_[key]
- Force-fixed unique_id to stable format: ninebot_[SN]_[key]
- Added entity registry enforcement to remove historical slug/suffixed names (including *_2/*_3 variants) and rebind to standardized IDs.
- Fully corrected vehicle lock mapping contract:
  - status=0 => locked
  - status=1 => unlocked
  - Applied consistently across raw sensor, binary_sensor, and read-only lock entity.
- Kept only required entities and synchronized bilingual default names in translations.
- Fixed lock status display mismatch in frontend by unifying conversion logic and dynamic lock/unlock icons.
