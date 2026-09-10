import { expect, it } from "vitest";
import { evidenceBox } from "@/ui/evidenceGeometry";

it("maps bottom-left source coordinates into the displayed unrotated crop", () => {
  expect(evidenceBox([10, 20, 120, 44], 200, 100)).toEqual({
    left: "5%",
    top: "56%",
    width: "55%",
    height: "24%",
  });
});

it.each([[0, 0, 0, 0], [-1, 0, 2, 4], [1, 2, 201, 4], [1, 2, 3, 101], [1, NaN, 3, 4], []])(
  "does not invent a highlight for invalid coordinates %j",
  (...box) => {
    expect(evidenceBox(box, 200, 100)).toBeNull();
  },
);
