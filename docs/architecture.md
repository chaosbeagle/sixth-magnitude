# Architecture

The agent chooses where to look, describes the image, and decides what to do next. Sixth Magnitude supplies the workbench and operations needed to save, inspect, and reuse those observations. The agent host retains control of the model, conversation, and computer-use tools.

## Components

The [skill](../sixth_magnitude/skill/SKILL.md) describes the inspection workflow. The Python package serves the workbench, reads images, and saves results. Domain profiles define observation fields and measurement rules.

| Component | Responsibility |
|---|---|
| `readers.py` | Open raster and native whole-slide sources |
| `imaging.py` | Render tiles, focus/context images, overlays, and coordinate transforms |
| `store.py` | Save changes, resolve retries, and bind review decisions to the inspected content |
| `server.py` | Serve the local browser interface and JSON actions |
| `profiles/` | Define the general inspection and lung pathology fields |
| `workbench.py`, `morphometry.py` | Handle pathology territories and measurements |

The two profiles share the same storage and review operations. A new two-dimensional task can add its observation fields and guidance without replacing that core. The browser is one way for an agent to interact with the tools; an MCP adapter could expose the same operations in a host's tool list.

## Image coordinates

Coordinates refer to the original image at reader level 0. Physical calibration comes from source metadata or an explicit user entry. Uncalibrated images use pixels. A focus/context image includes its transform back to the source and a mask describing which pixels support the observation.

The focus covers the selected geometry. Context covers the surrounding window with the focus excluded from its support mask. The context window must extend beyond the focus.

A source key binds the image ID, dimensions, calibration, file size, modification time, and a hash of the first 64 KiB. This partial fingerprint detects common source changes without reading an entire whole-slide file. Evidence images have individual hashes. Moving an image or changing its metadata requires an explicit workspace migration.

Raster inputs are limited to 40 million pixels and decoded regions to 25 million pixels. Native readers handle large whole-slide files. Normalize JPEGs with nonstandard EXIF orientation before registration so display and saved coordinates use the same orientation.

## Saving and review

Saving an observation links its description, geometry, and evidence. Visual confirmation records that the operator checked the saved overlay. Human acceptance is a separate decision. An edit clears both confirmation and acceptance for the changed observation, while earlier saved states remain available.

The workbench keeps drafts in the browser. Saved observations reside in the workspace and can be reopened from another session. A workspace initialized for testing, or an observation created in the engineering role, cannot enter accepted tables.

Reviewer timers require explicit start and stop actions. An open interval has no assigned duration. Roles are locally selected, without account authentication; the workbench is intended for one user at a time.

## Tool interface

The browser reads `/api/run`, `/api/schema`, and `/api/case/{asset_id}`. `/api/prepare` renders image evidence, `/api/action` saves changes, and `/api/export` writes a snapshot. Pathology adds `/api/packet` and `/api/territories` for compact region batches.

Changes include a `source_key`, `base_revision`, `operation_id`, `action`, and actor. Retrying the same operation returns its saved result. Reusing its ID for different content is rejected. A stale revision requires a reload before resubmitting.

`batch_save` commits up to 64 valid regions together. Visual and review actions carry the current `content_hash`; a visual confirmation also identifies the `evidence_id` that was inspected.

The server listens on `127.0.0.1` and checks the request host and browser origin. Exports may contain local paths and private observations. GeoJSON uses image coordinates, not geographic coordinates.

## Other image domains

A domain needs an observation schema, useful image views, task guidance, and tests of saving and review. The general profile already supports surface inspection, microscopy observations, and document-layout findings.

Volume tasks need additional information: volume identity, voxel-to-world transforms, slice or camera state, and three-dimensional geometry. A future DICOM or Slicer integration would need those definitions before a saved observation could be interpreted in the original volume.
