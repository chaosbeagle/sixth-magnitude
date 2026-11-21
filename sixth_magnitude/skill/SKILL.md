---
name: inspect
description: Set up Sesta Workbench, inspect images at multiple scales, save regions with evidence, verify overlays, and resume unfinished inspections.
---

# Inspect with Sesta Workbench

Use the workspace path and viewer URL supplied with the task. `python -m sixth_magnitude status --run RUN` shows the queue; `brief --run RUN --asset-id ID` summarizes one image. Reopen an existing workspace to continue.

Choose the model and reasoning effort in the agent host. The workbench stores the requested settings but does not change the model. If measuring usage, bind the actual session before starting and close the measurement when finished. Keep unavailable settings or counters unknown.

## Set up when requested

For a new setup, use the bundled repository containing this skill and `pyproject.toml`. In a plugin install, it is two directories above this skill's folder. The Python package also includes a copy of these instructions; if it is already installed, use that environment directly.

Create a Python 3.10+ virtual environment in the user's chosen working directory, then install the bundled repository with that environment's Python: `python -m pip install "ABSOLUTE_PLUGIN_ROOT"`. Quote paths. Keep environments, images and runs outside the plugin cache, which can be replaced during updates. Reuse a working installation instead of creating another one. Install optional native readers only when the input format needs them.

Create a practice workspace with `python -m sixth_magnitude demo --run RUN`, then start `python -m sixth_magnitude serve --run RUN --port 8790`. Check for an existing server/workspace first; use another available port if needed. On Windows, launch background helpers with hidden windows. Open the loopback URL using the host's browser or computer-use tools. Use the actual environment interpreter for every command.

The plugin supplies guidance and the workbench, not a browser driver or a model connection. If the host has no visual inspection tool, explain what needs enabling and let the user operate the viewer; do not claim a visual check from command output alone. Cloud model inspection sends the selected screenshots/content through the host's model service.

## Inspect and save

1. Check the image identity and scale. Examine the source, including the edges of a region before recording that a feature is absent there.
2. Select a region and inspect its focus and context. Context describes the surrounding window with the focus excluded. Zooming or panning alone does not change the saved region.
3. Enter a short observation. The general workbench accepts partial JSON under **Structured input**. Match each finding to its assessment state; leave unsupported details unassessed or indeterminate.
4. Save, then inspect the final overlay and corresponding observation. Confirm the overlay only after checking it. Saving and exporting do not establish visual inspection.
5. Continue from the saved state. Retry an uncertain submission with the same operation ID. If the saved content changed, reload and inspect it before submitting again. Do not edit stored review flags or historical files.

Reload for reconnection or an actual discrepancy, rather than after every region. Export at the end of a work block. Use AI mode for model observations and a test role and workspace for demonstrations. Human decisions must come from the actual reviewer; locally selected roles do not authenticate identity.

## Inspect H&E nucleus candidates

For nuclear segmentation and typing in Sesta Workbench or Sol's Hammer, use the user's chosen mode. If the user wants to choose, or starts a new nuclear workbench session without a mode or an established preference, present these three choices in chat before running inference. Use selectable options when the host offers them. All three include visual inspection, counting and cell typing.

- Tool-guided review lets the model select and compose tool-generated contours.
- Model-guided review starts from the source image and a blank mask. The model can draw or write image-processing code.
- Hybrid review starts a separate visual reviewer for this run. It compares the raw image and tool contours, selects a tool mask or blank canvas per field, and records why. The operator can combine or change routes using local evidence.

In hybrid nuclear review, inspect the full-field raw image, reviewer start and proposed final side by side. Use the comparison code from the full view, or call `compare` for a fresh view and optional ROI. Seal with `geometry` set to `start` or `final` and assign types to the IDs in that column. Check ambiguous cells on the clean source in local tissue context.

Pass the choice with `--mode` to `sixth-magnitude workbench` or `sols-hammer workbench`, together with `--fields` and `--output`. Install the shared tools and prepare fields as shown in the repository README.

For the older nucleus toolbox workflow, prepare a level-zero ROI with `sixth-magnitude toolbox prepare`. Supply `--reference-image` when the aligned native slide or a known canonical raster is available. Inspect the fields with `sixth-magnitude toolbox inspect`. Read the field status alongside the returned mask. An unresolved field retains a provisional baseline. Class changes require a separate confirmation on the individual clean crop. The standalone Sol's Hammer workbench uses its own batched triage policy. Illustrated comparisons and scoring definitions are in `docs/gallery/README.md`.

## Choose the profile

The general profile supports two-dimensional observations in pixels or calibrated units. Do not infer physical units from an uncalibrated screenshot. Inspecting selected regions does not establish whole-image coverage.

For `lung-pathology`, the root page supports territory batches and `/review` opens detailed observations. Batch visibly homogeneous areas, magnify mixed areas, and retain unresolved regions. Let the program calculate measurements. Report percentages and sampling rectangles cannot substitute for observed tumour territories.

For a lung region, survey the entire field for a supported growth pattern before proposing foreground. Revisit its borders in native-resolution crops alongside neighboring tissue. Check for a correctly empty field, then judge the extent and dominant pattern separately.

Territory codes: `T` tumour envelope; `L/A/P/M/S/C` lepidic/acinar/papillary/micropapillary/solid/complex glandular; `?` unresolved; `X` excluded; `B` unassessable boundary. Declare whole-section coverage only after inspecting all relevant tissue. Dictionary applicability, typing coverage, and boundary assessability are separate decisions. Incomplete evidence can leave measurements unavailable.

Read `docs/pathology.md` in the source distribution for setup and measurement definitions. Load image examples when they help resolve a recurring ambiguity. Preserve their review status; a model's interpretation remains a candidate until a human reviewer accepts it. Pathology acceptance requires physician review.
