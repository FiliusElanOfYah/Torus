import { NavLink, Link } from "react-router-dom";
import { Activity, Hexagon, ShieldAlert, Download, Terminal, Cpu } from "lucide-react";

const navItems = [
  { to: "/", label: "COMMAND", icon: Terminal, testId: "nav-dashboard" },
  { to: "/mesh", label: "DAEMON MESH", icon: Hexagon, testId: "nav-mesh" },
  { to: "/opsec", label: "OPSEC AUDIT", icon: ShieldAlert, testId: "nav-opsec" },
];

export default function Shell({ children, overallPercent = 0 }) {
  return (
    <div className="min-h-screen flex flex-col" data-testid="torus-shell">
      {/* Top bar */}
      <header
        className="flex items-center justify-between border-b border-dim px-6 py-3"
        style={{ background: "var(--bg-secondary)" }}
        data-testid="top-bar"
      >
        <Link to="/" className="flex items-center gap-3" data-testid="brand-link">
          <div
            className="w-8 h-8 flex items-center justify-center border border-bright"
            style={{ background: "var(--bg-command)" }}
          >
            <Cpu size={16} style={{ color: "var(--accent-amber)" }} />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="font-display text-sm text-amber-glow font-bold">TORUS</span>
            <span className="font-tactical text-[10px] uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
              Phase-0/1 Setup Generator
            </span>
          </div>
        </Link>

        <div className="hidden md:flex items-center gap-6 font-tactical text-xs uppercase tracking-widest">
          <span style={{ color: "var(--text-dim)" }}>Target</span>
          <span style={{ color: "var(--text-secondary)" }}>Samsung Galaxy S26 Ultra</span>
          <span style={{ color: "var(--text-dim)" }}>·</span>
          <span style={{ color: "var(--text-dim)" }}>Host</span>
          <span style={{ color: "var(--text-secondary)" }}>Termux (F-Droid)</span>
          <span style={{ color: "var(--text-dim)" }}>·</span>
          <div className="flex items-center gap-2" data-testid="top-bar-progress">
            <Activity size={12} style={{ color: "var(--accent-emerald)" }} />
            <span style={{ color: "var(--accent-emerald)" }}>{overallPercent}% VERIFIED</span>
          </div>
        </div>
      </header>

      {/* Body: side rail + main */}
      <div className="flex flex-1">
        <aside
          className="hidden lg:flex flex-col border-r border-dim px-4 py-6 w-56 gap-1"
          style={{ background: "var(--bg-secondary)" }}
          data-testid="side-rail"
        >
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              data-testid={item.testId}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 text-xs font-tactical uppercase tracking-widest transition-colors duration-150 ${
                  isActive
                    ? "border-l-2"
                    : "border-l-2 border-transparent hover:border-l-2"
                }`
              }
              style={({ isActive }) => ({
                color: isActive ? "var(--accent-amber)" : "var(--text-secondary)",
                borderLeftColor: isActive ? "var(--accent-amber)" : "transparent",
                background: isActive ? "var(--bg-card)" : "transparent",
              })}
            >
              <item.icon size={14} />
              <span>{item.label}</span>
            </NavLink>
          ))}

          <div className="mt-6 pt-6 border-t border-dim">
            <a
              href={`${process.env.REACT_APP_BACKEND_URL}/api/bundle/mesh`}
              className="flex items-center gap-2 px-3 py-2 text-xs font-tactical uppercase tracking-widest hover:text-amber-glow transition-colors"
              style={{ color: "var(--text-secondary)" }}
              data-testid="side-rail-full-bundle"
            >
              <Download size={14} />
              <span>Full Mesh ZIP</span>
            </a>
          </div>

          <div className="mt-auto pt-6 font-tactical text-[10px] uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
            <div>v0.1 · local-first</div>
            <div className="mt-1">truth above polls</div>
          </div>
        </aside>

        <main className="flex-1 min-w-0" data-testid="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
