"""Compare the route reviewer's geometry with the visible edit before assigning types."""
import hashlib

import numpy as np

from .free_edit import Canvas, Review, message, picture
from .source import pixel_hash


WORKFLOW = "independent-route-review-v2"


class SealCanvas(Canvas):
    def capture_start(self):
        self.start = self.current.copy()
        self.start.setflags(write=False)
        self.start_hash = pixel_hash(self.start)
        self.compared = None

    def view(self, gallery=False, roi=(), donor=None):
        if roi or donor:
            return super().view(gallery, roi, donor)
        parts = self.comparison_view()
        if gallery:
            parts.append(picture(self.rgb, list(self.bank.items())))
        return parts

    def comparison_view(self, roi=()):
        final = self.current if self.pending is None else self.pending
        digest = pixel_hash(final)
        token = hashlib.sha256(f"{self.key}/{self.start_hash}/{digest}".encode()).hexdigest()[:16]
        layers = [("Raw H&E", None), ("Reviewer start", self.start), ("Proposed final", final)]
        parts = [picture(self.rgb, layers, ids=True)]
        if roi:
            parts.append(picture(self.rgb, layers, roi, ids=True))
        self.opened = True
        self.compared = (digest, token)
        start_count, final_count = (np.count_nonzero(np.unique(mask)) for mask in (self.start, final))
        height, width = self.current.shape
        return [message(f"{self.key}, {width}x{height} px: start {start_count} nuclei, final {final_count} nuclei. "
                        f"Use comparison {token} to seal with geometry start or final. "
                        f"Use the IDs from that column. {self.key}-current.npy holds the committed edit."), *parts]

    def apply(self, row):
        action = row["action"]
        if self.finished or action not in ("compare", "seal"):
            return super().apply(row)
        if action == "compare":
            return self.comparison_view(row.get("roi", ()))
        final = self.current if self.pending is None else self.pending
        if self.compared != (pixel_hash(final), row.get("comparison")):
            raise ValueError("Compare the latest proposal and use its returned comparison code to seal.")
        if row.get("geometry") not in ("start", "final"):
            raise ValueError("Choose geometry start or final before assigning its instance types.")
        # Use the existing ID/type validation and retain the proposal if a seal is rejected.
        pending = self.pending
        if row["geometry"] == "start":
            self.pending = self.start
        try:
            return super().apply(row)
        finally:
            if not self.finished:
                self.pending = pending


class SealReview(Review):
    def __init__(self, paths, detector, work, output, classes, individual):
        self.individual = individual
        self.fields = {p.name: SealCanvas(p, "hybrid", detector, work, output, classes) for p in paths}

    def handle(self, decisions):
        if not decisions:
            keys = self.active()
            if not keys:
                return [message("COMPLETE")]
            # Record implicit opens too: a field can seal directly from this comparison.
            decisions = [{"field": key, "action": "view", "gallery": True}
                         for key in (keys[:1] if self.individual else keys)]
        return super().handle(decisions)
