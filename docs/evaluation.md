# Evaluate an inspection workflow

A useful inspection produces observations that someone can check and use. Measure the full work needed to reach that point, including corrections and searches for missed findings.

## What to measure

Track total workflow time, reviewer time, required observations accepted, missed findings, and changes to labels or geometry. Record model input/output tokens and tool calls separately. Include setup, recovery, and export in the total cost.

For open-ended inspection, ask reviewers to search the source image for omissions. Reviewing only the regions an agent selected cannot measure what it missed. Report predefined-region tasks separately from whole-image discovery.

## Compare model effort and workflow

A two-factor experiment can separate their effects:

| Factor | Conditions |
|---|---|
| Reasoning effort | Low and medium |
| Workflow | Ordinary viewer with manual entry, and Sixth Magnitude |

Use the same required fields and coordinate precision in both workflows. Freeze the task instructions before testing, use previously unexposed images, and run each condition in an independent agent context. Balance assignments across images and blind reviewers to the condition. Include difficult boundaries and small peripheral components.

Report where time changes: viewing, navigation, entry, saving, correction, and export. A shorter agent session is useful only if it does not shift more work to the reviewer. Compare missed findings alongside time so that incomplete inspection cannot appear to be higher productivity.

To assess the skill itself, hold the tools and model setting fixed. Compare a concise task instruction with the reusable skill on the same task distribution. Look for changes in repeated mistakes and the effort needed to correct them.

## Report enough to repeat the task

Describe the inspection scope, prior image exposure, required fields, model configuration, tool environment, calibration, coverage, and unresolved areas. Treat cached input as part of total input and reasoning output as part of total output. Keep missing counters unknown and specify when measurement stopped.

Publish aggregate results and images with suitable permissions. Keep private scans and raw sessions outside the repository. Software tests check saving, geometry, and calculations; task evaluation measures the quality and cost of the observations produced.
