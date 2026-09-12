import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/** Only location changes animate. Polling and render updates never initiate navigation or writes. */
export function RouteTransition({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const heading = element.querySelector<HTMLElement>("h1") ?? element;
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
    window.scrollTo?.({ top: 0, behavior: "instant" });
  }, [pathname]);
  return (
    <div className="route-transition" key={pathname} ref={host} tabIndex={-1}>
      {children}
    </div>
  );
}
