"""Small normalized homography solver, independently implemented with stdlib."""
from __future__ import annotations
from dataclasses import dataclass
import math
import sys

from .contract import Dimensions, Point


class GeometryError(ValueError):
    pass


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def normalize(point: Point, size: Dimensions) -> Point:
    return Point(point.x / (size.width - 1), point.y / (size.height - 1))


def denormalize(point: Point, size: Dimensions) -> Point:
    return Point(point.x * (size.width - 1), point.y * (size.height - 1))


def validate_quad(corners: tuple[Point, ...], size: Dimensions) -> tuple[Point, ...]:
    if not isinstance(corners, tuple) or len(corners) != 4 or not all(isinstance(p, Point) for p in corners):
        raise GeometryError("corners must be an immutable tuple: top-left, top-right, bottom-right, bottom-left")
    if not all(size.contains(p) for p in corners):
        raise GeometryError("all calibration corners must be inside the source frame")
    quad = tuple(normalize(p, size) for p in corners)
    # Positive cross products enforce a strictly convex, unmirrored perimeter in
    # a y-down image. Tiny quadrilaterals are deliberately rejected.
    if any(_cross(quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]) <= 1e-8 for i in range(4)):
        raise GeometryError("screen corners must form a well-conditioned clockwise convex quadrilateral")
    return quad


BOUNDARY_ROUNDOFF = 64 * sys.float_info.epsilon


def inside_quad(point: Point, corners: tuple[Point, ...]) -> bool:
    # A ~1.4e-14 normalized distance tolerance accounts only for IEEE-754
    # roundoff at the perimeter. It is not a physical uncertainty allowance.
    for index in range(4):
        a, b = corners[index], corners[(index + 1) % 4]
        tolerance = BOUNDARY_ROUNDOFF * math.hypot(b.x - a.x, b.y - a.y)
        if _cross(a, b, point) < -tolerance:
            return False
    return True


def _solve(rows: list[list[float]], rhs: list[float]) -> tuple[float, ...]:
    matrix = [row[:] + [answer] for row, answer in zip(rows, rhs)]
    n = len(rhs)
    for column in range(n):
        pivot_row = max(range(column, n), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot_row][column]) < 1e-12:
            raise GeometryError("singular or numerically unstable screen calibration")
        matrix[column], matrix[pivot_row] = matrix[pivot_row], matrix[column]
        pivot = matrix[column][column]
        matrix[column] = [value / pivot for value in matrix[column]]
        for row in range(n):
            if row == column:
                continue
            factor = matrix[row][column]
            matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[column])]
    answer = tuple(row[-1] for row in matrix)
    if not all(math.isfinite(value) for value in answer):
        raise GeometryError("non-finite homography")
    return answer


@dataclass(frozen=True)
class Homography:
    """Pure mathematics only: projecting a point does not validate a frame."""
    coefficients: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.coefficients, tuple) or len(self.coefficients) != 9:
            raise GeometryError("homography needs nine immutable coefficients")
        if not all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in self.coefficients):
            raise GeometryError("homography coefficients must be finite")

    @classmethod
    def fit(cls, source: tuple[Point, ...], destination: tuple[Point, ...]) -> Homography:
        if len(source) != 4 or len(destination) != 4:
            raise GeometryError("four source and destination points are required")
        rows, rhs = [], []
        for p, q in zip(source, destination):
            rows.extend(([p.x, p.y, 1, 0, 0, 0, -q.x * p.x, -q.x * p.y],
                         [0, 0, 0, p.x, p.y, 1, -q.y * p.x, -q.y * p.y]))
            rhs.extend((q.x, q.y))
        result = cls(_solve(rows, rhs) + (1.0,))
        for p, q in zip(source, destination):
            actual = result.project(p)
            if abs(actual.x - q.x) > 1e-8 or abs(actual.y - q.y) > 1e-8:
                raise GeometryError("calibration residual exceeds numerical tolerance")
        # A linear denominator with one nonzero sign at all vertices cannot have
        # a projective pole anywhere within the convex source quadrilateral.
        denominators = [result.denominator(p) for p in source]
        if not (all(d > 1e-12 for d in denominators) or all(d < -1e-12 for d in denominators)):
            raise GeometryError("homography crosses infinity inside the screen")
        return result

    def denominator(self, point: Point) -> float:
        h = self.coefficients
        return h[6] * point.x + h[7] * point.y + h[8]

    def project(self, point: Point) -> Point:
        h = self.coefficients
        denominator = self.denominator(point)
        if not math.isfinite(denominator) or abs(denominator) < 1e-12:
            raise GeometryError("point lies too close to the projective horizon")
        try:
            return Point((h[0] * point.x + h[1] * point.y + h[2]) / denominator,
                         (h[3] * point.x + h[4] * point.y + h[5]) / denominator)
        except ValueError as exc:
            raise GeometryError("projection is not finite") from exc


UNIT_SCREEN = (Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1))
