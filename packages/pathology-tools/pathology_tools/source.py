"""Compare the exact level-zero field against a caller-supplied, aligned RGB reference."""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

NATIVE_SUFFIXES = {".svs", ".ndpi", ".mrxs", ".scn", ".bif"}


def raster_rgb(image):
    if image.getexif().get(274, 1) != 1:
        raise ValueError("Normalize orientation before comparing source coordinates")
    rgba = image.convert("RGBA")
    return np.asarray(Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB"))


def reference_roi(path, xy, wh):
    path = Path(path)
    x, y = xy
    w, h = wh
    if x < 0 or y < 0 or min(w, h) < 1:
        raise ValueError("Invalid level-zero reference coordinates")
    native = path.suffix.lower() in NATIVE_SUFFIXES
    if path.suffix.lower() in {".tif", ".tiff"}:
        try:
            from openslide import OpenSlide
            native = OpenSlide.detect_format(str(path)) is not None
        except (ImportError, OSError):
            pass
    if native:
        try:
            from openslide import OpenSlide
        except (ImportError, OSError) as exc:
            raise ValueError("Install the openslide extra to read a native slide reference") from exc
        with OpenSlide(str(path)) as slide:
            if x+w > slide.dimensions[0] or y+h > slide.dimensions[1]:
                raise ValueError("Field lies outside the level-zero reference")
            return raster_rgb(slide.read_region((x, y), 0, (w, h)))
    with Image.open(path) as im:
        if im.getexif().get(274, 1) != 1 or x+w > im.width or y+h > im.height:
            raise ValueError("Reference orientation or extent differs from the source coordinates")
        if im.width*im.height > 40_000_000:
            raise ValueError("Use a native slide reader or an aligned bounded raster reference")
        return raster_rgb(im.crop((x, y, x+w, y+h)))


def pixel_hash(rgb):
    return hashlib.sha256(np.ascontiguousarray(rgb).tobytes()).hexdigest()


def align_rgb(rgb, origin, reference=None, native=False):
    original = pixel_hash(rgb)
    info = {"status": "native_level0" if native else "unverified", "input_rgb_sha256": original,
            "working_rgb_sha256": original, "reference": None}
    if reference is None:
        return rgb, info
    ref = reference_roi(reference, origin, (rgb.shape[1], rgb.shape[0]))
    target = ref.astype(np.int16)
    pixels = rgb.astype(np.int16)
    identity = int(np.abs(pixels - target).max())
    swapped = int(np.abs(pixels[:, :, ::-1] - target).max())
    if min(identity, swapped) > 1:
        raise ValueError(f"Reference pixels disagree at this origin (RGB max error {identity}, "
                         f"R/B swapped {swapped}). Verify alignment, scale and encoding.")
    if identity <= 1 and swapped <= 1:
        status = "reference_indistinguishable"
    else:
        status = "reference_rgb" if identity <= 1 else "reference_rb_swapped"
    return ref, {**info, "status": status, "reference": str(Path(reference).resolve()),
                 "rgb_max_error": identity, "swapped_max_error": swapped,
                 "working_rgb_sha256": pixel_hash(ref)}
