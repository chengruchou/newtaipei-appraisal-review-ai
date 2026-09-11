import { useEffect, useState } from "react";
import { PrivacyWorkbench } from "@/privacy/PrivacyWorkbench";

export function PrivacyRoute() {
  const [base, setBase] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(() => {
      controller.abort();
      if (active) setFailed(true);
    }, 10_000);
    void (async () => {
      try {
        const response = await fetch("/local-config.json", {
          credentials: "omit",
          redirect: "error",
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Public configuration unavailable");
        const value: unknown = await response.json();
        if (
          !value ||
          typeof value !== "object" ||
          !("privacy_bridge_base" in value) ||
          typeof value.privacy_bridge_base !== "string"
        )
          throw new Error("Missing public bridge configuration");
        const configured = new URL(value.privacy_bridge_base);
        if (
          configured.protocol !== "http:" ||
          !["127.0.0.1", "[::1]"].includes(configured.hostname) ||
          configured.username ||
          configured.password ||
          configured.pathname !== "/" ||
          configured.search ||
          configured.hash
        )
          throw new Error("Invalid public bridge configuration");
        if (active && !controller.signal.aborted) setBase(configured.origin);
      } catch {
        if (active) setFailed(true);
      } finally {
        clearTimeout(timer);
      }
    })();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  if (failed)
    return (
      <p role="alert">
        Local privacy review is unavailable. Check the configured local bridge with the operator.
      </p>
    );
  return base ? (
    <PrivacyWorkbench bridgeBase={base} />
  ) : (
    <p role="status">Loading local privacy configuration…</p>
  );
}
