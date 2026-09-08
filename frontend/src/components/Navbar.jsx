import React from 'react';
import { ShieldCheck, MessageSquare, Home, ExternalLink } from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab }) {
  return (
    <header className="navbar">
      <div className="navbar-inner">
        {/* Brand / Department Header */}
        <div className="brand-group" onClick={() => setActiveTab('landing')}>
          <div className="brand-badge">
            <ShieldCheck size={16} strokeWidth={2.2} />
            <span>BIS</span>
          </div>
          <div className="brand-title">
            <span className="brand-name">Bureau of Indian Standards</span>
            <span className="brand-sub">Standards &amp; Regulatory Intelligence Platform</span>
          </div>
        </div>

        {/* Institutional Nav Tabs */}
        <nav className="nav-center" aria-label="Main Navigation">
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === 'landing' ? 'active' : ''}`}
            onClick={() => setActiveTab('landing')}
          >
            <Home size={14} />
            <span>Overview</span>
          </button>
          <button
            type="button"
            className={`nav-tab-btn ${activeTab === 'chat' ? 'active' : ''}`}
            onClick={() => setActiveTab('chat')}
          >
            <MessageSquare size={14} />
            <span>Assistant Chat</span>
          </button>
        </nav>

        {/* Official Portal */}
        <div className="nav-right">
          <a
            href="https://www.bis.gov.in"
            target="_blank"
            rel="noopener noreferrer"
            className="nav-link-ext"
            title="Official Bureau of Indian Standards Portal"
          >
            <span>bis.gov.in</span>
            <ExternalLink size={12} />
          </a>
        </div>
      </div>
    </header>
  );
}
