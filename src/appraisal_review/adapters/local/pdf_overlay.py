"""Low-level deterministic PDF geometry and text-correction primitives.

This module contains the first-stage rendering spike. It deliberately supports
only text runs that pypdf can extract as one complete text-show operation. A
caller must fail when a correction is ambiguous; covering stale text is never
an acceptable fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Literal

from pypdf import PageObject
from reportlab.pdfbase import pdfmetrics

from appraisal_review.domain.pdf_models import PDFFieldPlacementError, PDFReadError

Point = tuple[float, float]
BoundingBox = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]
PathKind = Literal["move", "line", "rectangle", "other"]

# Quote operators also change the text position. Refuse them until their full
# line-movement semantics are represented in the measured run.
_TEXT_SHOW_OPERATORS = {b"Tj", b"TJ"}
_DEFAULT_TEXT_STATE = {
    b"Tz": 100.0,
    b"Tc": 0.0,
    b"Tw": 0.0,
    b"Ts": 0.0,
    b"Tr": 0.0,
}
_STANDARD_BASE14_FONTS = frozenset(
    {
        "Courier",
        "Courier-Bold",
        "Courier-BoldOblique",
        "Courier-Oblique",
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-BoldOblique",
        "Helvetica-Oblique",
        "Times-Bold",
        "Times-BoldItalic",
        "Times-Italic",
        "Times-Roman",
    }
)
_CUSTOM_FONT_METRIC_KEYS = frozenset(
    {
        "/CharProcs",
        "/DescendantFonts",
        "/FirstChar",
        "/FontDescriptor",
        "/FontMatrix",
        "/LastChar",
        "/ToUnicode",
        "/Widths",
    }
)
_PATH_FILL_ONLY_OPERATORS = frozenset({b"f", b"F", b"f*"})
_PATH_FILL_AND_STROKE_OPERATORS = frozenset({b"B", b"B*", b"b", b"b*"})
_PATH_FILL_OPERATORS = _PATH_FILL_ONLY_OPERATORS | _PATH_FILL_AND_STROKE_OPERATORS
_PATH_STROKE_OPERATORS = frozenset({b"S", b"s"})
_MAX_TABLE_BORDER_WIDTH = 2.0
_GEOMETRY_ABS_TOLERANCE = 1e-6


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


@dataclass(frozen=True)
class _GraphicsState:
    """Paint state needed to establish conservative stroke bounds."""

    matrix: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    line_width: float = 1.0
    line_cap: int = 0
    line_join: int = 0
    miter_limit: float = 10.0
    dash_pattern: tuple[float, ...] = ()
    dash_phase: float = 0.0


def _font_name(font_dictionary: Any, text: str) -> str:
    if font_dictionary is None or "/BaseFont" not in font_dictionary:
        raise PDFFieldPlacementError("Text font cannot be measured deterministically")
    name = str(font_dictionary["/BaseFont"]).lstrip("/")
    subtype = str(font_dictionary.get("/Subtype", ""))
    encoding = str(font_dictionary.get("/Encoding", ""))
    if (
        subtype != "/Type1"
        or name not in _STANDARD_BASE14_FONTS
        or any(key in font_dictionary for key in _CUSTOM_FONT_METRIC_KEYS)
        or encoding not in {"", "/WinAnsiEncoding", "/MacRomanEncoding"}
        or any(ord(character) < 32 or ord(character) > 126 for character in text)
    ):
        raise PDFFieldPlacementError("Source PDF font metrics cannot be established safely")
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
        width = float(
            pdfmetrics.stringWidth(
                clean_text,
                _font_name(font_dictionary, clean_text),
                font_size,
            )
        )
    except PDFFieldPlacementError:
        raise
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
    _reject_unsupported_text_geometry(content.operations)
    _validate_page_font_resources(page)
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


def _validate_page_font_resources(page: PageObject) -> None:
    """Reject correction when any direct page font has untrusted metrics."""
    try:
        resources = page.get("/Resources", {}).get_object()
        fonts = resources.get("/Font", {}).get_object()
        for reference in fonts.values():
            _font_name(reference.get_object(), "A")
    except PDFFieldPlacementError:
        raise
    except Exception as error:
        raise PDFFieldPlacementError(
            "Source PDF font metrics cannot be established safely"
        ) from error


def _reject_unsupported_text_geometry(operations: list[tuple[Any, bytes]]) -> None:
    """Fail closed for text state not represented by `_text_bounds`."""
    for operands, operator in operations:
        if operator in _DEFAULT_TEXT_STATE:
            try:
                value = float(operands[0])
            except (IndexError, TypeError, ValueError) as error:
                raise PDFFieldPlacementError(
                    "PDF text geometry cannot be measured deterministically"
                ) from error
            if not math.isclose(value, _DEFAULT_TEXT_STATE[operator]):
                raise PDFFieldPlacementError("PDF text geometry uses an unsupported text state")
        if operator == b"TJ":
            try:
                adjustments = [
                    float(item) for item in operands[0] if not isinstance(item, (bytes, str))
                ]
            except (IndexError, TypeError, ValueError) as error:
                raise PDFFieldPlacementError(
                    "PDF text positioning cannot be measured deterministically"
                ) from error
            if any(not math.isclose(value, 0.0) for value in adjustments):
                raise PDFFieldPlacementError("PDF text positioning adjustments are unsupported")


def visual_content_in_box(page: PageObject, crop_local_box: BoundingBox) -> bool:
    """Return whether painted or annotated content occupies the field box."""
    geometry = PageGeometry.from_page(page)
    page_box = geometry.box_to_page_user_space(crop_local_box)
    content = page.get_contents()
    if content is not None:
        state = _GraphicsState()
        stack: list[_GraphicsState] = []
        path_points: list[Point] = []
        path_kind: PathKind | None = None
        for operands, operator in content.operations:
            if operator == b"q":
                stack.append(state)
            elif operator == b"Q":
                if not stack:
                    raise PDFFieldPlacementError("PDF graphics state is unbalanced")
                state = stack.pop()
            elif operator == b"cm":
                transform = _finite_numbers(operands, expected=6)
                state = replace(
                    state,
                    matrix=_multiply_matrices(transform, list(state.matrix)),
                )
            elif operator == b"w":
                line_width = _single_finite_number(operands)
                if line_width < 0:
                    raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
                state = replace(state, line_width=line_width)
            elif operator in {b"J", b"j"}:
                style = _single_pdf_integer(operands, allowed={0, 1, 2})
                state = replace(
                    state,
                    line_cap=style if operator == b"J" else state.line_cap,
                    line_join=style if operator == b"j" else state.line_join,
                )
            elif operator == b"M":
                miter_limit = _single_finite_number(operands)
                if miter_limit < 1:
                    raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
                state = replace(state, miter_limit=miter_limit)
            elif operator == b"d":
                state = _with_dash_pattern(state, operands)
            elif operator == b"gs":
                state = _with_extended_graphics_state(page, state, operands)
            elif operator == b"INLINE IMAGE":
                if _boxes_intersect(page_box, _unit_square_bounds(state.matrix)):
                    return True
            elif operator == b"Do" and _boxes_intersect(
                page_box, _unit_square_bounds(state.matrix)
            ):
                # Text extraction already refuses XObjects. Treat their painted
                # unit square as occupancy, and fail closed if it does not give
                # a trustworthy answer for the requested region.
                return True
            elif operator == b"m":
                path_kind = "move" if not path_points else "other"
                path_points.extend(_path_points(operands, state.matrix, pairs=1))
            elif operator == b"l":
                path_kind = "line" if path_kind == "move" and len(path_points) == 1 else "other"
                path_points.extend(_path_points(operands, state.matrix, pairs=1))
            elif operator == b"c":
                path_points.extend(_path_points(operands, state.matrix, pairs=3))
                path_kind = "other"
            elif operator in {b"v", b"y"}:
                path_points.extend(_path_points(operands, state.matrix, pairs=2))
                path_kind = "other"
            elif operator == b"re":
                path_kind = "rectangle" if not path_points else "other"
                path_points.extend(_rectangle_points(operands, state.matrix))
            elif operator in _PATH_FILL_OPERATORS:
                if not path_points:
                    raise PDFFieldPlacementError(
                        "PDF painted path geometry cannot be established safely"
                    )
                if _boxes_intersect(page_box, _points_bounds(path_points)) or (
                    operator in _PATH_FILL_AND_STROKE_OPERATORS
                    and _stroke_intersects_box(
                        page_box,
                        path_points,
                        state,
                        single_rectangle=path_kind == "rectangle",
                    )
                ):
                    return True
                path_points = []
                path_kind = None
            elif operator in _PATH_STROKE_OPERATORS:
                if not path_points:
                    raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
                if _is_allowed_table_border(
                    path_points,
                    page_box,
                    state,
                    path_kind=path_kind,
                ):
                    path_points = []
                    path_kind = None
                    continue
                if _stroke_intersects_box(
                    page_box,
                    path_points,
                    state,
                    single_rectangle=path_kind == "rectangle",
                ):
                    return True
                path_points = []
                path_kind = None
            elif operator == b"n":
                path_points = []
                path_kind = None
            elif operator == b"sh":
                raise PDFFieldPlacementError("PDF shading geometry cannot be established safely")
        if stack:
            raise PDFFieldPlacementError("PDF graphics state is unbalanced")

    annotations = page.get("/Annots", [])
    try:
        for reference in annotations:
            annotation = reference.get_object()
            rect = annotation.get("/Rect")
            if rect is None or len(rect) != 4:
                raise PDFFieldPlacementError(
                    "PDF annotation geometry cannot be measured deterministically"
                )
            bounds: BoundingBox = (
                float(rect[0]),
                float(rect[1]),
                float(rect[2]),
                float(rect[3]),
            )
            if _boxes_intersect(page_box, bounds):
                return True
    except PDFFieldPlacementError:
        raise
    except Exception as error:
        raise PDFFieldPlacementError(
            "PDF annotation geometry cannot be measured deterministically"
        ) from error
    return False


def _unit_square_bounds(matrix: Matrix) -> BoundingBox:
    corners = [
        _transform_point(matrix, point)
        for point in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _finite_numbers(operands: Any, *, expected: int) -> list[float]:
    try:
        values = [float(value) for value in operands]
        if len(values) != expected or not all(math.isfinite(value) for value in values):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise PDFFieldPlacementError(
            "PDF visual geometry cannot be measured deterministically"
        ) from error
    return values


def _single_finite_number(operands: Any) -> float:
    return _finite_numbers(operands, expected=1)[0]


def _single_pdf_integer(operands: Any, *, allowed: set[int]) -> int:
    value = _single_finite_number(operands)
    integer = int(value)
    if not math.isclose(value, integer) or integer not in allowed:
        raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
    return integer


def _with_dash_pattern(state: _GraphicsState, operands: Any) -> _GraphicsState:
    try:
        if len(operands) != 2:
            raise ValueError
        pattern = tuple(float(value) for value in operands[0])
        phase = float(operands[1])
        if (
            not math.isfinite(phase)
            or any(not math.isfinite(value) or value < 0 for value in pattern)
            or (pattern and math.isclose(sum(pattern), 0.0))
        ):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely") from error
    return replace(state, dash_pattern=pattern, dash_phase=phase)


def _with_extended_graphics_state(
    page: PageObject, state: _GraphicsState, operands: Any
) -> _GraphicsState:
    """Apply stroke geometry supplied by a named ExtGState resource."""
    try:
        if len(operands) != 1:
            raise ValueError
        resources: Any = page["/Resources"].get_object()
        states: Any = resources["/ExtGState"].get_object()
        parameters = states[operands[0]].get_object()
    except Exception as error:
        raise PDFFieldPlacementError(
            "PDF extended graphics state cannot be established safely"
        ) from error

    updated = state
    try:
        if "/LW" in parameters:
            line_width = _single_finite_number([parameters["/LW"].get_object()])
            if line_width < 0:
                raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
            updated = replace(updated, line_width=line_width)
        if "/LC" in parameters:
            updated = replace(
                updated,
                line_cap=_single_pdf_integer([parameters["/LC"].get_object()], allowed={0, 1, 2}),
            )
        if "/LJ" in parameters:
            updated = replace(
                updated,
                line_join=_single_pdf_integer([parameters["/LJ"].get_object()], allowed={0, 1, 2}),
            )
        if "/ML" in parameters:
            miter_limit = _single_finite_number([parameters["/ML"].get_object()])
            if miter_limit < 1:
                raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
            updated = replace(updated, miter_limit=miter_limit)
        if "/D" in parameters:
            updated = _with_dash_pattern(updated, parameters["/D"].get_object())
        if "/SA" in parameters:
            stroke_adjustment = getattr(parameters["/SA"].get_object(), "value", None)
            if not isinstance(stroke_adjustment, bool) or stroke_adjustment:
                raise PDFFieldPlacementError(
                    "PDF stroke adjustment geometry cannot be established safely"
                )
    except PDFFieldPlacementError:
        raise
    except Exception as error:
        raise PDFFieldPlacementError(
            "PDF extended graphics state cannot be established safely"
        ) from error
    return updated


def _maximum_linear_scale(matrix: Matrix) -> float:
    a, b, c, d, _, _ = matrix
    squared_sum = a * a + b * b + c * c + d * d
    determinant = a * d - b * c
    discriminant = max(0.0, squared_sum * squared_sum - 4.0 * determinant * determinant)
    scale = math.sqrt((squared_sum + math.sqrt(discriminant)) / 2.0)
    if not math.isfinite(scale) or scale <= 0:
        raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
    return scale


def _effective_line_width(state: _GraphicsState) -> float:
    if state.line_width <= 0:
        # A zero-width PDF hairline is device-dependent rather than a stable
        # user-space width, so it cannot support a safe blank-field decision.
        raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
    width = state.line_width * _maximum_linear_scale(state.matrix)
    if not math.isfinite(width):
        raise PDFFieldPlacementError("PDF stroke geometry cannot be established safely")
    return width


def _stroke_bounds(
    points: list[Point], state: _GraphicsState, *, include_joins: bool | None = None
) -> BoundingBox:
    width = _effective_line_width(state)
    radius = width / 2.0
    if include_joins is None:
        include_joins = len(points) > 2
    if include_joins and state.line_join == 0:
        radius *= state.miter_limit
    x1, y1, x2, y2 = _points_bounds(points)
    return (x1 - radius, y1 - radius, x2 + radius, y2 + radius)


def _stroke_intersects_box(
    field_box: BoundingBox,
    points: list[Point],
    state: _GraphicsState,
    *,
    single_rectangle: bool,
) -> bool:
    if not single_rectangle or len(points) != 4:
        return _boxes_intersect(field_box, _stroke_bounds(points, state))
    # `_rectangle_points` returns lower-left, lower-right, upper-left, upper-right.
    # Test each painted edge separately so the unpainted rectangle interior is
    # not mistaken for occupancy merely because it lies inside the path bounds.
    rectangle_edges = (
        (points[0], points[1]),
        (points[1], points[3]),
        (points[3], points[2]),
        (points[2], points[0]),
    )
    return any(
        _boxes_intersect(
            field_box,
            _stroke_bounds(list(edge), state, include_joins=True),
        )
        for edge in rectangle_edges
    )


def _is_allowed_table_border(
    points: list[Point],
    field_box: BoundingBox,
    state: _GraphicsState,
    *,
    path_kind: PathKind | None,
) -> bool:
    """Allow only a narrow, solid path coincident with the field boundary."""
    if (
        state.line_cap != 0
        or state.line_join != 0
        or state.dash_pattern
        or not _coordinates_close(state.dash_phase, 0.0)
        or _effective_line_width(state) > _MAX_TABLE_BORDER_WIDTH
    ):
        return False
    if path_kind == "line" and len(points) == 2:
        return _line_covers_field_boundary(points, field_box)
    if path_kind != "rectangle" or len(points) != 4:
        return False
    expected_corners = (
        (field_box[0], field_box[1]),
        (field_box[2], field_box[1]),
        (field_box[0], field_box[3]),
        (field_box[2], field_box[3]),
    )
    return _same_point_set(points, expected_corners)


def _line_covers_field_boundary(points: list[Point], field_box: BoundingBox) -> bool:
    start, end = points
    minimum_x, maximum_x = sorted((start[0], end[0]))
    minimum_y, maximum_y = sorted((start[1], end[1]))
    horizontal = _coordinates_close(start[1], end[1]) and (
        _coordinates_close(start[1], field_box[1]) or _coordinates_close(start[1], field_box[3])
    )
    vertical = _coordinates_close(start[0], end[0]) and (
        _coordinates_close(start[0], field_box[0]) or _coordinates_close(start[0], field_box[2])
    )
    return (
        horizontal
        and minimum_x <= field_box[0] + _GEOMETRY_ABS_TOLERANCE
        and maximum_x >= field_box[2] - _GEOMETRY_ABS_TOLERANCE
    ) or (
        vertical
        and minimum_y <= field_box[1] + _GEOMETRY_ABS_TOLERANCE
        and maximum_y >= field_box[3] - _GEOMETRY_ABS_TOLERANCE
    )


def _same_point_set(left: Any, right: Any) -> bool:
    return all(
        any(_points_close(point, candidate) for candidate in right) for point in left
    ) and all(any(_points_close(point, candidate) for point in left) for candidate in right)


def _points_close(left: Point, right: Point) -> bool:
    return _coordinates_close(left[0], right[0]) and _coordinates_close(left[1], right[1])


def _coordinates_close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=_GEOMETRY_ABS_TOLERANCE)


def _path_points(operands: list[Any], matrix: Matrix, *, pairs: int) -> list[Point]:
    try:
        values = [float(value) for value in operands]
        if len(values) != pairs * 2 or not all(math.isfinite(value) for value in values):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise PDFFieldPlacementError(
            "PDF painted path geometry cannot be established safely"
        ) from error
    return [
        _transform_point(matrix, (values[index], values[index + 1]))
        for index in range(0, len(values), 2)
    ]


def _rectangle_points(operands: list[Any], matrix: Matrix) -> list[Point]:
    try:
        x, y, width, height = (float(value) for value in operands)
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise PDFFieldPlacementError(
            "PDF painted path geometry cannot be established safely"
        ) from error
    return [
        _transform_point(matrix, point)
        for point in (
            (x, y),
            (x + width, y),
            (x, y + height),
            (x + width, y + height),
        )
    ]


def _points_bounds(points: list[Point]) -> BoundingBox:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


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
