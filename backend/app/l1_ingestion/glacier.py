"""L1 glacier / GLOF imagery.

STUBBED ON PURPOSE. Free real-time satellite access for GLOF-relevant imagery
is not realistically available for a hackathon demo. This module defines the
CNN scorer interface and a stub that reads a static scene id / image set.

Do not present this path as live satellite inference.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Protocol

from app.config import GLACIER_STUB_DIR


@dataclass
class GlacierScore:
    probability: float
    severity: float
    confidence: float
    scene_id: str
    stubbed: bool = True
    note: str = "stubbed CNN — static scene, not live satellite"


class GlacierScorer(Protocol):
    def score(self, scene_id: str) -> GlacierScore: ...


class StubGlacierCNN:
    """Filename/scene-id scorer standing in for a pretrained GLOF CNN.

    A real implementation would run a CNN asynchronously on satellite tiles
    (revisit is measured in days). Here we map a static scene catalogue to a
    plausible risk value so L3/L4 have a stable interface to call.
    """

    def __init__(self, root: str = GLACIER_STUB_DIR) -> None:
        self.root = root
        self.catalog_path = os.path.join(root, "scenes.json")

    def score(self, scene_id: str) -> GlacierScore:
        catalog = {}
        if os.path.exists(self.catalog_path):
            with open(self.catalog_path, encoding="utf-8") as f:
                catalog = json.load(f)
        if scene_id in catalog:
            item = catalog[scene_id]
            return GlacierScore(
                probability=float(item["probability"]),
                severity=float(item["severity"]),
                confidence=float(item.get("confidence", 0.45)),
                scene_id=scene_id,
                stubbed=True,
            )
        # Deterministic placeholder from the scene id so demos are repeatable.
        digest = hashlib.sha256(scene_id.encode()).digest()
        p = 0.08 + (digest[0] / 255.0) * 0.12
        return GlacierScore(
            probability=round(p, 3),
            severity=0.7,
            confidence=0.35,
            scene_id=scene_id,
            stubbed=True,
        )


def default_scorer() -> StubGlacierCNN:
    return StubGlacierCNN()
