# Contributing

Read the [architecture](docs/architecture.md) before changing image coordinates, review behaviour, or measurement denominators. Use synthetic fixtures for automated tests. For public examples, include image sources and permission details. Keep private scans, credentials, session logs, and working data out of the repository.

Follow the [development checks](docs/validation.md) for the components you change. Exercise interface changes in a browser and inspect the saved result. Report what was checked, including the affected input format and platform.

Keep domain terms in their profiles and domain modules. Preserve uncertainty and source calibration. Changes to saved fields or coordinate meanings need an explicit migration.

Contributions to project code are made under the [MIT license](LICENSE.md). Preserve third-party notices and image credits.

The plugin skill in `skills/sixth-magnitude/SKILL.md` mirrors the Python package skill. Update both together and run `python scripts/check_plugin.py` before publishing.
