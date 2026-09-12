/** Route and stylesheet unit regression, not browser or real-case acceptance. */
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { readFileSync } from "node:fs";
import { Link, MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { RouteTransition } from "@/ui/RouteTransition";

const styles = readFileSync("src/ui/styles.css", "utf8");

afterEach(() => vi.restoreAllMocks());

it("animates pathname changes while polling and query updates retain the current host", () => {
  const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  function Content() {
    const [polls, setPolls] = useState(0);
    return (
      <>
        <h1>Unit route view</h1>
        <output>{polls}</output>
        <button onClick={() => setPolls(polls + 1)}>Poll read</button>
        <Link to="/case/evidence?finding=1">Different evidence</Link>
        <Link to="/case/tasks">Human tasks</Link>
      </>
    );
  }
  const { container } = render(
    <MemoryRouter initialEntries={["/case/evidence?finding=0"]}>
      <RouteTransition>
        <Content />
      </RouteTransition>
    </MemoryRouter>,
  );
  const first = container.querySelector(".route-transition");
  expect(screen.getByRole("heading")).toHaveFocus();
  expect(scroll).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Poll read" }));
  expect(container.querySelector(".route-transition")).toBe(first);
  fireEvent.click(screen.getByRole("link", { name: "Different evidence" }));
  expect(container.querySelector(".route-transition")).toBe(first);
  expect(scroll).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("link", { name: "Human tasks" }));
  expect(container.querySelector(".route-transition")).not.toBe(first);
  expect(screen.getByRole("heading")).toHaveFocus();
  expect(scroll).toHaveBeenCalledTimes(2);
  expect(scroll).toHaveBeenLastCalledWith({ top: 0, behavior: "instant" });
});

it("declares a 180ms fade with at most 8px travel and disables motion when requested", () => {
  const element = document.createElement("style");
  element.textContent = styles;
  document.head.append(element);
  try {
    const rules = Array.from(element.sheet!.cssRules);
    const root = rules.find(
      (rule) => (rule as CSSStyleRule).selectorText === ":root",
    ) as CSSStyleRule;
    expect(root.style.getPropertyValue("--route-duration")).toBe("180ms");
    const transition = rules.find(
      (rule) => (rule as CSSStyleRule).selectorText === ".route-transition",
    ) as CSSStyleRule;
    expect(transition.style.getPropertyValue("animation")).toContain(
      "route-enter var(--route-duration)",
    );
    const keyframes = rules.find(
      (rule) => (rule as CSSKeyframesRule).name === "route-enter",
    ) as CSSKeyframesRule;
    const from = keyframes.cssRules[0] as CSSKeyframeRule;
    const to = keyframes.cssRules[1] as CSSKeyframeRule;
    const distance = Number(
      from.style.getPropertyValue("transform").match(/translateY\((\d+)px\)/)?.[1],
    );
    expect(distance).toBeGreaterThanOrEqual(0);
    expect(distance).toBeLessThanOrEqual(8);
    expect(from.style.getPropertyValue("opacity")).toBe("0");
    expect(to.style.getPropertyValue("opacity")).toBe("1");
    const reduced = rules.find(
      (rule) => (rule as CSSMediaRule).conditionText === "(prefers-reduced-motion: reduce)",
    ) as CSSMediaRule;
    const declaration = reduced.cssRules[0] as CSSStyleRule;
    expect(declaration.selectorText).toContain("*");
    expect(declaration.style.getPropertyValue("animation")).toBe("none");
    expect(declaration.style.getPropertyPriority("animation")).toBe("important");
    expect(declaration.style.getPropertyValue("transition")).toBe("none");
    expect(declaration.style.getPropertyValue("scroll-behavior")).toBe("auto");
  } finally {
    element.remove();
  }
});
