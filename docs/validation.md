# Development checks

Run the tests after changing image coordinates, saved observations, review decisions, or measurement rules.

```sh
python -m pip install -e ".[test]"
python -m pytest -q
node --check sixth_magnitude/ui/inspect.js
node --check sixth_magnitude/ui/agent.js
node --check sixth_magnitude/ui/app.js
```

The suite covers observation fields, geometry and masks, source changes, conflicting edits, retry recovery, batch saves, review binding, missing denominators, measurements, usage accounting, and malformed requests. Synthetic test data keep these checks independent of private images.

## Check the browser

Start both example workspaces:

```sh
python -m sixth_magnitude demo --run runs/check-general
python -m sixth_magnitude serve --run runs/check-general --port 8790
```

```sh
python -m sixth_magnitude demo --profile lung-pathology --run runs/check-pathology
python -m sixth_magnitude serve --run runs/check-pathology --port 8791
```

In each workbench, inspect an image, change a region, save an observation, and confirm the saved overlay. Reload and check that the geometry and description agree with what was saved. Edit the observation again and check that its earlier confirmation no longer applies. Export the workspace and inspect the resulting table.

In the pathology workbench, also check a batch containing unresolved territories. Incomplete coverage or applicability should retain the appropriate NA reasons. Keep browser checks separate from backend test results: a passing calculation test does not establish that a label or control is readable.

## Check an installed package

Build a wheel and install it into a separate environment. Run the two examples from outside the source checkout. Confirm that the installed package contains the interface files, observation schemas, skill, and third-party notices. Check image preparation, saving, and export through the installed application.

The GitHub workflow runs tests and synthetic examples on Windows and Ubuntu with Python 3.10 and 3.12. Native readers need their own checks with the target format and platform. Compare image metadata and bounded reads, including a region at an image edge and a lower-resolution level.

For changes to agent guidance, use the [workflow evaluation](evaluation.md) to check effects on inspection quality and human review effort.
