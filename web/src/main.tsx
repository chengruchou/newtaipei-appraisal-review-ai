import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./ui/styles.css";

const host = document.getElementById("root");
if (host === null) {
  throw new Error("The page is missing its root element");
}
createRoot(host).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
