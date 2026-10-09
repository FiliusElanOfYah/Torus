import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";

import Dashboard from "@/pages/Dashboard";
import PhaseDetail from "@/pages/PhaseDetail";
import Mesh from "@/pages/Mesh";
import Opsec from "@/pages/Opsec";

export default function App() {
  return (
    <div className="App" data-testid="app-root">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/phase/:number" element={<PhaseDetail />} />
          <Route path="/mesh" element={<Mesh />} />
          <Route path="/opsec" element={<Opsec />} />
        </Routes>
      </BrowserRouter>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#161D21",
            color: "#E2E8F0",
            border: "1px solid #3A4B54",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: "12px",
          },
        }}
      />
    </div>
  );
}
