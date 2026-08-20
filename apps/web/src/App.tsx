import { Link, Route, Routes, useLocation } from "react-router-dom";
import CustomerPage from "./pages/CustomerPage";
import AdminPage from "./pages/AdminPage";
import { useHealth } from "./useHealth";

function Header() {
  const location = useLocation();
  const { ok, loading, error } = useHealth();
  const onAdmin = location.pathname.startsWith("/admin");

  const dotClass = loading
    ? "dot dot-unknown"
    : ok
    ? "dot dot-ok"
    : "dot dot-down";
  const statusText = loading
    ? "Checking…"
    : ok
    ? "Connected"
    : "Backend offline";

  return (
    <header className="app-header">
      <div className="app-header-left">
        <span className="app-logo" aria-hidden="true">
          ◈
        </span>
        <div className="app-title-wrap">
          <span className="app-title" title="Multimodal Voice-to-Voice Insurance AI">
            Assured
          </span>
          <span className="badge badge-synthetic">SYNTHETIC DEMO DATA</span>
        </div>
      </div>
      <div className="app-header-right">
        <span
          className="conn-status"
          title={error ?? statusText}
          aria-live="polite"
        >
          <span className={dotClass} aria-hidden="true" />
          {statusText}
        </span>
        <nav className="app-nav">
          <Link className={!onAdmin ? "nav-link active" : "nav-link"} to="/">
            Customer
          </Link>
          <Link
            className={onAdmin ? "nav-link active" : "nav-link"}
            to="/admin"
          >
            Admin
          </Link>
        </nav>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <Header />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<CustomerPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route
            path="*"
            element={<div className="empty-state">Page not found.</div>}
          />
        </Routes>
      </main>
    </div>
  );
}
