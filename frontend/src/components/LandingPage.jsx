import React, { useState } from 'react';
import { 
  Search, 
  ArrowRight, 
  ShieldCheck, 
  Cpu, 
  Sparkles, 
  Award, 
  Layers, 
  BookOpen, 
  FileCheck2,
  Database,
  ExternalLink,
  ChevronRight,
  FileText
} from 'lucide-react';

export default function LandingPage({ onStartChat, healthStatus }) {
  const [query, setQuery] = useState('');

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    if (query.trim()) {
      onStartChat(query.trim());
    } else {
      onStartChat();
    }
  };

  const handlePresetClick = (presetQuery) => {
    onStartChat(presetQuery);
  };

  const domainCards = [
    {
      icon: <Cpu size={18} />,
      title: 'Electronics & IT (CRS)',
      desc: 'Compulsory Registration Scheme requirements for mobile devices, LED lighting, power supplies, and IT equipment under Scheme-II.',
      query: 'What standard applies to LED bulbs under CRS certification?',
      standardRef: 'IS 16102 / CRS Scheme-II',
    },
    {
      icon: <Award size={18} />,
      title: 'Gold & Silver Hallmarking',
      desc: '6-digit HUID identification protocol, mandatory karatage grades (14K, 18K, 22K, 24K), consumer verification rules, and assaying requirements.',
      query: 'What is 6-digit HUID in gold jewellery and how can a consumer verify it?',
      standardRef: 'Hallmarking Regulation 2018',
    },
    {
      icon: <Layers size={18} />,
      title: 'Steel & Industrial QCOs',
      desc: 'Mandatory Quality Control Orders issued by Ministry of Steel for TMT rebars, structural sections, plates, and industrial valves.',
      query: 'Is ISI certification mandatory for structural steel and TMT bars in India?',
      standardRef: 'IS 1786 / Scheme-I (ISI Mark)',
    },
    {
      icon: <FileCheck2 size={18} />,
      title: 'Toys & Consumer Safety',
      desc: 'Mandatory physical, mechanical, chemical, and electrical safety standards under Toys Quality Control Order.',
      query: 'What are the BIS testing standards and certification rules for toys?',
      standardRef: 'IS 9873 & IS 15644',
    },
  ];

  const faqs = [
    {
      q: 'Where does the platform retrieve its regulatory data?',
      a: 'All answers are retrieved directly from indexed official Bureau of Indian Standards (BIS) gazettes, mandatory Quality Control Orders (QCOs), and published scheme manuals. No unverified internet data is utilized.',
    },
    {
      q: 'How does the system ensure standard number accuracy?',
      a: 'The query pipeline verifies standard identifiers against official database indices before returning findings to eliminate incorrect citations.',
    },
    {
      q: 'How do I determine if a product falls under mandatory certification?',
      a: 'You can query any product category to check for applicable Quality Control Orders (QCOs), Scheme-I (ISI Mark), or Scheme-II (CRS) mandates.',
    },
  ];

  return (
    <div className="landing-view">
      {/* Standards Search Portal Section */}
      <section className="portal-search-section">
        <div className="portal-container">
          {/* Institutional Header Banner */}
          <div className="portal-header-block">
            <div className="portal-kicker">NATIONAL STANDARDIZATION &amp; CONFORMITY ASSESSMENT</div>
            <h1 className="portal-main-heading">Indian Standards &amp; Regulatory Intelligence</h1>
            <p className="portal-sub-text">
              Direct evidence-grounded search for Indian Standards (IS), mandatory Quality Control Orders (QCOs), 
              Scheme-I / Scheme-II compliance rules, and gold hallmarking provisions.
            </p>
          </div>

          {/* Search Box Card */}
          <div className="portal-search-card">
            <div className="search-header-label">SEARCH THE BIS KNOWLEDGE BASE</div>
            <div className="search-category-hints">
              <span>Search by:</span>
              <strong>Product</strong> · 
              <strong>IS Number</strong> · 
              <strong>QCO</strong> · 
              <strong>Certification Requirement</strong> · 
              <strong>Hallmarking</strong>
            </div>

            <form className="portal-search-form" onSubmit={handleSearchSubmit}>
              <div className="search-input-group">
                <Search size={18} className="search-input-icon" />
                <input
                  type="text"
                  className="portal-input"
                  placeholder="Enter product name, standard (e.g., IS 16102), or regulatory query..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <button type="submit" className="portal-submit-btn">
                <span>Search Repository</span>
                <ArrowRight size={15} />
              </button>
            </form>

            <div className="search-examples-row">
              <span className="examples-label">Examples:</span>
              <button type="button" className="example-tag-btn" onClick={() => handlePresetClick('What is the standard for packaged drinking water?')}>
                Packaged Drinking Water
              </button>
              <button type="button" className="example-tag-btn" onClick={() => handlePresetClick('What standard applies to LED bulbs under CRS certification?')}>
                LED Lamps
              </button>
              <button type="button" className="example-tag-btn" onClick={() => handlePresetClick('What is 6-digit HUID in gold jewellery and how can a consumer verify it?')}>
                Gold Hallmarking
              </button>
              <button type="button" className="example-tag-btn" onClick={() => handlePresetClick('Is ISI certification mandatory for structural steel and TMT bars in India?')}>
                TMT Steel Bars
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Structured Domain Grid */}
      <section className="portal-section">
        <div className="portal-container">
          <div className="section-title-row">
            <div>
              <h2 className="section-heading">Key Regulatory &amp; Compliance Domains</h2>
              <p className="section-subtext">Access specific compliance documentation and testing protocols across major sectors</p>
            </div>
          </div>

          <div className="domain-grid-institutional">
            {domainCards.map((card, idx) => (
              <div key={idx} className="domain-box" onClick={() => handlePresetClick(card.query)}>
                <div className="domain-box-top">
                  <span className="domain-box-icon">{card.icon}</span>
                  <span className="domain-ref-badge">{card.standardRef}</span>
                </div>
                <h3 className="domain-box-title">{card.title}</h3>
                <p className="domain-box-desc">{card.desc}</p>
                <div className="domain-box-action">
                  <span>Query domain intelligence</span>
                  <ChevronRight size={14} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Verification Framework (Evidence Pipeline) */}
      <section className="portal-section section-bordered-top">
        <div className="portal-container">
          <div className="section-title-row">
            <div>
              <h2 className="section-heading">Conformity Intelligence Architecture</h2>
              <p className="section-subtext">Three-stage evidence grounding pipeline for regulatory precision</p>
            </div>
          </div>

          <div className="pipeline-grid">
            <div className="pipeline-item">
              <div className="pipeline-header">
                <span className="pipeline-badge">01</span>
                <span className="pipeline-title">Official Gazette &amp; Document Corpus</span>
              </div>
              <p className="pipeline-desc">
                Directly indexed official BIS gazettes, product testing manuals, and Quality Control Orders stored with clause-level metadata in vector storage.
              </p>
            </div>

            <div className="pipeline-item">
              <div className="pipeline-header">
                <span className="pipeline-badge">02</span>
                <span className="pipeline-title">Hybrid BM25 &amp; Vector Retrieval</span>
              </div>
              <p className="pipeline-desc">
                High-precision keyword matching for exact IS numbers combined with semantic embedding search for domain terminology.
              </p>
            </div>

            <div className="pipeline-item">
              <div className="pipeline-header">
                <span className="pipeline-badge">03</span>
                <span className="pipeline-title">Clause-Level Evidence Verification</span>
              </div>
              <p className="pipeline-desc">
                Synthesis strictly grounded in verified excerpts, outputting official source references, applicable schemes, and mandatory dates.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Institutional Repository Metrics Strip */}
      <section className="portal-metrics-strip">
        <div className="portal-container metrics-inner">
          <div className="metric-box">
            <div className="metric-num">{healthStatus?.chroma_chunks || '137'}</div>
            <div className="metric-label">Indexed Standard Documents</div>
          </div>
          <div className="metric-box">
            <div className="metric-num">{healthStatus?.qco_table_rows || '260+'}</div>
            <div className="metric-label">Active Mandatory QCO Records</div>
          </div>
          <div className="metric-box">
            <div className="metric-num">Scheme I &amp; II</div>
            <div className="metric-label">ISI &amp; CRS Conformity Schemes</div>
          </div>
          <div className="metric-box">
            <div className="metric-num">Official BIS</div>
            <div className="metric-label">Authoritative Source Grounding</div>
          </div>
        </div>
      </section>

      {/* Frequently Referenced Questions */}
      <section className="portal-section">
        <div className="portal-container">
          <div className="section-title-row">
            <div>
              <h2 className="section-heading">Frequently Referenced Inquiries</h2>
              <p className="section-subtext">Operational guidance on utilizing the standards intelligence repository</p>
            </div>
          </div>

          <div className="faq-grid-institutional">
            {faqs.map((faq, idx) => (
              <div key={idx} className="faq-item-institutional">
                <h3 className="faq-q-title">{faq.q}</h3>
                <p className="faq-a-body">{faq.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Institutional Footer */}
      <footer className="portal-footer">
        <div className="portal-container footer-layout">
          <div className="footer-meta">
            <strong>Bureau of Indian Standards (BIS) Intelligence Platform</strong>
            <span>National Standards Body of India · Department of Consumer Affairs, Ministry of Consumer Affairs, Food &amp; Public Distribution</span>
          </div>
          <div className="footer-disclaimer">
            Information provided is derived from published Indian Standards and official regulatory notifications. For statutory enforcement or formal certification filings, refer directly to <a href="https://www.bis.gov.in" target="_blank" rel="noreferrer">bis.gov.in</a> or BIS Care Helpline: <strong>1800-11-4000</strong>.
          </div>
        </div>
      </footer>
    </div>
  );
}
