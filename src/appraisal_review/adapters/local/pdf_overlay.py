"""Low-level deterministic PDF geometry and text-correction primitives.

This module contains the first-stage rendering spike. It deliberately supports
only text runs that pypdf can extract as one complete text-show operation. A
caller must fail when a correction is ambiguous; covering stale text is never
an acceptable fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pypdf import PageObject
from reportlab.pdfbase import pdfmetrics

from appraisal_review.domain.pdf_models import PDFFieldPlacementError, PDFReadError

Point = tuple[float, float]
BoundingBox = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]

# Quote operators also change the text position. Refuse them until their full
# line-movement semantics are represented in the measured run.
_TEXT_SHOW_OPERATORS = {b"Tj", b"TJ"}


def _multiply_matrices(left: list[float], right: list[float]) -> Matrix:
    """Compose PDF affine matrices using the PDF six-value convention."""
    return (
        left[0] * right[0] + left[1] * right[2],
        left[0] * right[1] + left[1] * right[3],
        left[2] * right[0] + left[3] * right[2],
        left[2] * right[1] + left[3] * right[3],
        left[4] * right[0] + left[5] * right[2] + right[4],
        left[4] * right[1] + left[5] * right[3] + right[5],
    )


def _transform_point(matrix: Matrix, point: Point) -> Point:
    x, y = point
    return (
        x * matrix[0] + y * matrix[2] + matrix[4],
        x * matrix[1] + y * matrix[3] + matrix[5],
    )


@dataclass(frozen=True)
class PageGeometry:
    """A page's unrotated CropBox geometry and viewer rotation."""

    crop_box: BoundingBox
    rotation: int
    user_unit: float

    @classmethod
    def from_page(cls, page: PageObject) -> PageGeometry:
        crop = page.cropbox
        rotation = page.rotation % 360
        user_unit = float(page.user_unit)
        if rotation not in {0, 90, 180, 270}:
            raise PDFFieldPlacementError("PDF page rotation must be a right angle")
        if not math.isfinite(user_unit) or user_unit <= 0:
            raise PDFFieldPlacementError("PDF UserUnit must be finite and positive")
        crop_box = (
            float(crop.left),
            float(crop.bottom),
            float(crop.right),
            float(crop.top),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            raise PDFFieldPlacementError("PDF CropBox must have positive dimensions")
        return cls(crop_box=crop_box, rotation=rotation, user_unit=user_unit)

    @property
    def width(self) -> float:
        return (self.crop_box[2] - self.crop_box[0]) * self.user_unit

    @property
    def height(self) -> float:
        return (self.crop_box[3] - self.crop_box[1]) * self.user_unit

    @property
    def rotated_size(self) -> Point:
        if self.rotation in {90, 270}:
            return (self.height, self.width)
        return (self.width, self.height)

    def to_page_user_space(self, point: Point) -> Point:
        """Translate an unrotated CropBox-local point into PDF page user space."""
        x, y = point
        if not 0 <= x <= self.width or not 0 <= y <= self.height:
            raise PDFFieldPlacementError("Point is outside the unrotated CropBox")
        return (
            self.crop_box[0] + x / self.user_unit,
            self.crop_box[1] + y / self.user_unit,
        )

    def to_rotated_crop_space(self, point: Point) -> Point:
        """Map a CropBox-local point to the rotated viewer coordinate space."""
        x, y = point
        self.to_page_user_space(point)
        if self.rotation == 0:
            return (x, y)
        if self.rotation == 90:
            return (y, self.width - x)
        if self.rotation == 180:
            return (self.width - x, self.height - y)
        return (self.height - y, x)

    def box_to_page_user_space(self, box: BoundingBox) -> BoundingBox:
        x1, y1, x2, y2 = box
        if x2 <= x1 or y2 <= y1:
            raise PDFFieldPlacementError("PDF box must have positive dimensions")
        lower_left = self.to_page_user_space((x1, y1))
        upper_right = self.to_page_user_space((x2, y2))
        return (*lower_left, *upper_right)


@dataclass(frozen=True)
class TextShowOperation:
    """One extractable, complete PDF text-show operation."""

    operation_index: int
    text: str
    bounds: BoundingBox


def _font_name(font_dictionary: Any) -> str:
    if font_dictionary is None or "/BaseFont" not in font_dictionary:
        raise PDFFieldPlacementError("Text font cannot be measured deterministically")
    name = str(font_dictionary["/BaseFont"]).lstrip("/")
    if "+" in name:
        name = name.split("+", maxsplit=1)[1]
    return name


def _text_bounds(
    text: str,
    matrix: Matrix,
    font_dictionary: Any,
    font_size: float,
) -> BoundingBox:
    clean_text = text.rstrip("\r\n")
    if not clean_text or font_size <= 0:
        raise PDFFieldPlacementError("Text operation cannot be measured deterministically")
    try:
        width = float(pdfmetrics.stringWidth(clean_text, _font_name(font_dictionary), font_size))
    except (KeyError, TypeError, ValueError) as error:
        raise PDFFieldPlacementError("Text font cannot be measured deterministically") from error
    local_corners = (
        (0.0, -font_size * 0.25),
        (width, -font_size * 0.25),
        (0.0, font_size),
        (width, font_size),
    )
    transformed = [_transform_point(matrix, point) for point in local_corners]
    xs = [point[0] for point in transformed]
    ys = [point[1] for point in transformed]
    return (min(xs), min(ys), max(xs), max(ys))


def inspect_text_show_operations(page: PageObject) -> list[TextShowOperation]:
    """Locate direct, extractable page text while preserving operation identity."""
    content = page.get_contents()
    if content is None:
        return []
    if any(operator == b"Do" for _, operator in content.operations):
        raise PDFFieldPlacementError("Text inside page XObjects is unsupported for correction")

    operation_index = -1
    pending: list[tuple[int, Matrix]] = []
    runs: list[TextShowOperation] = []
    ambiguous = False

    def before(
        operator: bytes,
        _operands: list[Any],
        current_matrix: list[float],
        text_matrix: list[float],
    ) -> None:
        nonlocal operation_index
        operation_index += 1
        if operator in _TEXT_SHOW_OPERATORS:
            pending.append((operation_index, _multiply_matrices(text_matrix, current_matrix)))

    def visit_text(
        text: str,
        _current_matrix: list[float],
        _text_matrix: list[float],
        font_dictionary: Any,
        font_size: float,
    ) -> None:
        nonlocal ambiguous
        if not text:
            return
        if len(pending) != 1:
            ambiguous = True
            return
        index, matrix = pending.pop()
        clean_text = text.rstrip("\r\n")
        runs.append(
            TextShowOperation(
                operation_index=index,
                text=clean_text,
                bounds=_text_bounds(clean_text, matrix, font_dictionary, float(font_size)),
            )
        )

    try:
        page.extract_text(visitor_operand_before=before, visitor_text=visit_text)
    except PDFFieldPlacementError:
        raise
    except Exception as error:
        raise PDFReadError("PDF text operations cannot be inspected") from error
    if ambiguous or pending:
        raise PDFFieldPlacementError("PDF text operations cannot be mapped unambiguously")
    return runs


def text_runs_in_box(page: PageObject, crop_local_box: BoundingBox) -> list[TextShowOperation]:
    """Return complete direct text runs intersecting a CropBox-local box."""
    geometry = PageGeometry.from_page(page)
    page_box = geometry.box_to_page_user_space(crop_local_box)
    return [
        run for run in inspect_text_show_operations(page) if _boxes_intersect(page_box, run.bounds)
    ]


def correction_runs_in_box(
    page: PageObject, crop_local_box: BoundingBox
) -> list[TextShowOperation]:
    """Preflight a correction without mutating the page content stream."""
    geometry = PageGeometry.from_page(page)
    page_box = geometry.box_to_page_user_space(crop_local_box)
    intersecting = text_runs_in_box(page, crop_local_box)
    if not intersecting:
        raise PDFFieldPlacementError("Correction box does not contain extractable text")
    if any(not _box_contains(page_box, run.bounds) for run in intersecting):
        raise PDFFieldPlacementError("Correction box partially intersects a text operation")
    return intersecting


def _boxes_intersect(left: BoundingBox, right: BoundingBox) -> bool:
    return not (
        left[2] <= right[0] or left[0] >= right[2] or left[3] <= right[1] or left[1] >= right[3]
    )


def _box_contains(outer: BoundingBox, inner: BoundingBox) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def remove_text_in_box(page: PageObject, crop_local_box: BoundingBox) -> list[str]:
    """Remove complete text-show operations fully contained by a correction box.

    This first-stage primitive intentionally refuses partial intersections and
    unsupported text structures. It edits text operators only; path operations
    such as table borders remain untouched.
    """
    intersecting = correction_runs_in_box(page, crop_local_box)

    indexes = {run.operation_index for run in intersecting}
    content = page.get_contents()
    if content is None:
        raise PDFReadError("PDF page has no content stream")
    if any(
        index >= len(content.operations) or content.operations[index][1] not in _TEXT_SHOW_OPERATORS
        for index in indexes
    ):
        raise PDFFieldPlacementError("PDF text operation mapping changed during correction")
    content.operations = [
        operation for index, operation in enumerate(content.operations) if index not in indexes
    ]
    page.replace_contents(content)
    return [run.text for run in intersecting]
