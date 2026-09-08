import React, { useState } from 'react';
import { 
  User, 
  Copy, 
  Check, 
  FileText, 
  ExternalLink,
  CheckCircle2,
  Table,
  Layers,
  HelpCircle,
  AlertTriangle,
  ShieldCheck,
  FileCheck
} from 'lucide-react';
import { renderMarkdownToNodes } from '../utils/markdown';

const ROUTE_LABELS = {
  structured_lookup: { label: 'Structured QCO Lookup', icon: <Table size={12} /> },
  procedure_rag: { label: 'Official Procedure Documentation', icon: <FileText size={12} /> },
  faq_rag: { label: 'BIS FAQ & Regulatory Guidance', icon: <HelpCircle size={12} /> },
  out_of_scope: { label: 'Non-BIS Regulatory Inquiry', icon: <AlertTriangle size={12} /> },
};

export default function MessageItem({ message }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  const handleCopy = () => {
    if (!message.content) return;
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isUser) {
    return (
      <div className="message-row user">
        <div className="message-bubble user">
          <div className="message-user-header">RESEARCH INQUIRY</div>
          <div className="message-text">{message.content}</div>
        </div>
      </div>
    );
  }

  // Assistant message (Structured Evidence / Standard Research Output)
  const routeInfo = ROUTE_LABELS[message.route] || {
    label: 'Official BIS Intelligence',
    icon: <Layers size={12} />,
  };

  const confidenceScore = typeof message.confidence === 'number' && message.confidence > 0
    ? Math.round(message.confidence * 100)
    : null;

  return (
    <div className="message-row assistant">
      <div className="message-bubble assistant">
        {/* Document Header Bar */}
        <div className="doc-result-header">
          <span className="doc-result-label">
            <FileCheck size={13} className="doc-icon-primary" />
            <span>STANDARDS INTELLIGENCE RESULT</span>
          </span>
          {message.route && (
            <span className="doc-route-tag">
              {routeInfo.icon}
              <span>{routeInfo.label}</span>
            </span>
          )}
        </div>

        {/* Structured Document Body */}
        <div className="message-text doc-content-area">
          {renderMarkdownToNodes(message.content)}
        </div>

        {/* Structured Official Document References */}
        {Array.isArray(message.sources) && message.sources.length > 0 && (
          <div className="doc-citations-section">
            <div className="citations-header">
              <FileText size={13} />
              <span>OFFICIAL REGULATORY CITATIONS &amp; CLAUSE SOURCES</span>
            </div>
            <div className="citations-grid">
              {message.sources.map((src, idx) => (
                <a
                  key={idx}
                  href={src.url || 'https://www.bis.gov.in'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="citation-card"
                  title="Open official BIS document"
                >
                  <div className="citation-title-row">
                    <span className="citation-title">{src.title}</span>
                    <ExternalLink size={12} className="citation-ext-icon" />
                  </div>
                  <div className="citation-meta-row">
                    <span className="citation-type-badge">{src.category || 'BIS Publication'}</span>
                    <span className="citation-source-label">Official Source · bis.gov.in</span>
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Metadata & Actions Footer */}
        <div className="message-meta-row">
          {confidenceScore !== null && (
            <span 
              className={`meta-pill ${confidenceScore >= 60 ? 'match-high' : 'match-mid'}`}
              title="Cosine similarity to source text in BIS database"
            >
              <CheckCircle2 size={12} />
              <span>{confidenceScore}% Source Match</span>
            </span>
          )}

          {Array.isArray(message.stripped_is) && message.stripped_is.length > 0 && (
            <span className="meta-pill guardrail" title="Unverified standard numbers omitted">
              <ShieldCheck size={12} />
              <span>{message.stripped_is.length} unverified IS omitted</span>
            </span>
          )}

          {message.low_confidence && (
            <span className="meta-pill alert" title="Low similarity score. Confirm with official portal.">
              <AlertTriangle size={12} />
              <span>Verify at bis.gov.in</span>
            </span>
          )}

          {/* Copy Button */}
          <button 
            type="button" 
            className="copy-btn" 
            onClick={handleCopy} 
            title="Copy findings to clipboard"
          >
            {copied ? (
              <>
                <Check size={12} style={{ color: 'var(--success)' }} />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy size={12} />
                <span>Copy Finding</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
