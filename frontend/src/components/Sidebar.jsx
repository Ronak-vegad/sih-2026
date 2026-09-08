import React from 'react';
import { 
  PlusCircle, 
  ChevronRight
} from 'lucide-react';

export default function Sidebar({
  isOpen,
  onClose,
  onNewChat,
  onSelectPrompt,
}) {
  const commonLookups = [
    { title: 'LED Lights CRS standard', query: 'What standard applies to LED bulbs under CRS?' },
    { title: 'Gold Hallmarking HUID rules', query: 'What is 6-digit HUID in gold hallmarking?' },
    { title: 'Packaged Drinking Water standard', query: 'What is the Indian Standard for packaged drinking water?' },
    { title: 'TMT Steel Bars mandatory QCO', query: 'Is ISI certification mandatory for TMT steel bars?' },
    { title: 'Electric Toys safety rules', query: 'What are the safety standards for electric toys in India?' },
    { title: 'Solar PV Modules certification', query: 'What is the standard and scheme for Solar PV modules?' },
    { title: 'FMCS Scheme for foreign makers', query: 'Explain Foreign Manufacturers Certification Scheme (FMCS)' },
  ];

  return (
    <>
      {/* Mobile backdrop */}
      <div 
        className={`sidebar-backdrop ${isOpen ? 'open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside className={`chat-sidebar ${isOpen ? 'open' : ''}`}>
        {/* New Session Action */}
        <div className="sidebar-header">
          <button type="button" className="new-chat-btn" onClick={onNewChat}>
            <PlusCircle size={15} />
            <span>New Research Session</span>
          </button>
        </div>

        <div className="sidebar-content">
          {/* Section: Common BIS Lookups */}
          <div className="sidebar-section">
            <div className="sidebar-section-header">
              <span className="sidebar-section-title">Common BIS Lookups</span>
            </div>
            <nav className="sidebar-nav-list">
              {commonLookups.map((item, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="sidebar-lookup-item"
                  onClick={() => {
                    onSelectPrompt(item.query);
                    onClose();
                  }}
                >
                  <span className="lookup-bullet" />
                  <span className="lookup-text">{item.title}</span>
                  <ChevronRight size={13} className="lookup-chevron" />
                </button>
              ))}
            </nav>
          </div>

          {/* Institutional Note */}
          <div className="sidebar-footer-note">
            <span className="footer-dept-label">BUREAU OF INDIAN STANDARDS</span>
            <span>National Standards Body of India</span>
            <span className="footer-helpline">Toll-Free Helpline: <strong>1800-11-4000</strong></span>
          </div>
        </div>
      </aside>
    </>
  );
}
