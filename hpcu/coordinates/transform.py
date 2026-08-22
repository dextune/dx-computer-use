"""3×3 homogeneous coordinate transform.

All transforms are pure functions on (x, y) → (x', y').
No display or window handle is used.
"""

import math
from typing import Optional

import numpy as np


def identity() -> np.ndarray:
    """Return the 3×3 identity transform."""
    return np.eye(3, dtype=np.float64)


def translation(tx: float, ty: float) -> np.ndarray:
    """Translate by (tx, ty)."""
    t = identity()
    t[0, 2] = tx
    t[1, 2] = ty
    return t


def scale(sx: float, sy: float) -> np.ndarray:
    """Scale by (sx, sy)."""
    t = identity()
    t[0, 0] = sx
    t[1, 1] = sy
    return t


def rotation(theta_rad: float) -> np.ndarray:
    """Rotate counter-clockwise by theta radians."""
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    t = identity()
    t[0, 0] = c
    t[0, 1] = -s
    t[1, 0] = s
    t[1, 1] = c
    return t


def compose(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compose two transforms: apply a then b."""
    return b @ a


def inverse(t: np.ndarray) -> np.ndarray:
    """Return the inverse of a 3×3 transform."""
    return np.linalg.inv(t)


def transform_point(t: np.ndarray, x: float, y: float) -> tuple[float, float]:
    """Apply a 3×3 homogeneous transform to a point (x, y)."""
    v = np.array([x, y, 1.0], dtype=np.float64)
    result = t @ v
    return (float(result[0]), float(result[1]))


def transform_bbox(
    t: np.ndarray, x: float, y: float, width: float, height: float
) -> tuple[float, float, float, float]:
    """Transform a bounding box through a 3×3 matrix.

    Returns (x, y, width, height) after transformation.
    Handles rotation by recomputing the tight axis-aligned bounding box
    of the four transformed corners.
    """
    corners = [
        (x, y),
        (x + width, y),
        (x, y + height),
        (x + width, y + height),
    ]
    transformed = [transform_point(t, cx, cy) for cx, cy in corners]
    xs = [p[0] for p in transformed]
    ys = [p[1] for p in transformed]
    new_x = min(xs)
    new_y = min(ys)
    new_w = max(xs) - new_x
    new_h = max(ys) - new_y
    return (new_x, new_y, new_w, new_h)