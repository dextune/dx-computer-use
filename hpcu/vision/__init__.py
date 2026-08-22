"""Vision / perception primitives (common layer).

This package is perception layer code shared across platforms.  It must
never import `hpcu.platform`.  Modules here operate on `FrameHandle`
objects and produce structured perception DTOs, never raw bytes.
"""

from hpcu.vision.ocr import OcrEngine, TextRegion
from hpcu.vision.shape_detector import ShapeCandidate, ShapeDetector
from hpcu.vision.template_matcher import TemplateMatch, TemplateMatcher
from hpcu.vision.text_component_association import ComponentGroup, associate

__all__ = [
    "ComponentGroup",
    "OcrEngine",
    "ShapeCandidate",
    "ShapeDetector",
    "TemplateMatch",
    "TemplateMatcher",
    "TextRegion",
    "associate",
]