/** Source citations use unrotated, CropBox-local PDF points with a bottom-left origin. */
export function evidenceBox(box: readonly number[], width: number, height: number) {
  const [x0, y0, x1, y1] = box;
  if (
    box.length !== 4 ||
    x0 === undefined ||
    y0 === undefined ||
    x1 === undefined ||
    y1 === undefined ||
    ![x0, y0, x1, y1, width, height].every(Number.isFinite) ||
    width <= 0 ||
    height <= 0 ||
    x0 < 0 ||
    y0 < 0 ||
    x1 > width ||
    y1 > height ||
    x1 <= x0 ||
    y1 <= y0
  )
    return null;
  return {
    left: `${(100 * x0) / width}%`,
    top: `${(100 * (height - y1)) / height}%`,
    width: `${(100 * (x1 - x0)) / width}%`,
    height: `${(100 * (y1 - y0)) / height}%`,
  };
}
