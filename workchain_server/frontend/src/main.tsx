import React from "react";
import ReactDOM from "react-dom/client";
import "reactflow/dist/style.css";
import "./styles.css";
import { App } from "./App";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element not found");

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
