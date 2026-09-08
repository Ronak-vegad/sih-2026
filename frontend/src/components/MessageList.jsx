import React, { useEffect, useRef } from 'react';
import MessageItem from './MessageItem';
import { ShieldCheck, ChevronRight, FileSearch } from 'lucide-react';

export default function MessageList({
  messages,
  isLoading,
  streamingMessage,
  onSelectPrompt,
}) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, isLoading]);

  const researchPrompts = [
    {
      category: 'Electronics & IT (CRS)',
      text: 'What is the Indian Standard for LED bulbs under CRS certification?',
      code: 'IS 16102 (Part 1)',
    },
    {
      category: 'Gold & Silver Hallmarking',
      text: 'What are the 6-digit HUID rules and hallmark verification steps?',
      code: 'HUID & 14K/18K/22K',
    },
    {
      category: 'Steel & Construction QCO',
      text: 'Is ISI certification mandatory for structural steel and TMT bars in India?',
      code: 'IS 1786 / Scheme-I',
    },
    {
      category: 'Consumer Product Safety',
      text: 'What are the BIS testing standards and certification rules for toys?',
      code: 'IS 9873 / Toys QCO',
    },
  ];

  return (
    <div className="messages-container">
      {/* If no messages yet, show institutional repository welcome */}
      {messages.length === 0 && !streamingMessage && (
        <div className="chat-welcome-card">
          <div className="welcome-header-tag">
            <ShieldCheck size={14} />
            <span>STANDARDS &amp; CONFORMITY INTELLIGENCE REPOSITORY</span>
          </div>
          <h2 className="welcome-title">Search Indian Standards &amp; Mandatory Regulations</h2>
          <p className="welcome-subtitle">
            Query applicable IS specifications, Quality Control Orders (QCOs), licensing schemes (ISI/CRS), and testing guidelines. Every response cites verified official documentation.
          </p>

          <div className="welcome-prompts-grid">
            {researchPrompts.map((p, idx) => (
              <div
                key={idx}
                className="welcome-prompt-box"
                onClick={() => onSelectPrompt(p.text)}
              >
                <div className="prompt-top-row">
                  <span className="prompt-cat-badge">{p.category}</span>
                  <span className="prompt-code-tag">{p.code}</span>
                </div>
                <div className="prompt-body-text">{p.text}</div>
                <div className="prompt-action-link">
                  <span>Execute standard query</span>
                  <ChevronRight size={13} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Render existing messages */}
      {messages.map((msg, idx) => (
        <MessageItem key={idx} message={msg} />
      ))}

      {/* Render in-flight streaming message if present */}
      {streamingMessage && (
        <MessageItem message={streamingMessage} />
      )}

      {/* Loading indicator when waiting for first token */}
      {isLoading && !streamingMessage?.content && (
        <div className="message-row assistant">
          <div className="message-bubble assistant loading-state-bubble">
            <div className="loading-state-content">
              <div className="loading-state-spinner" />
              <div>
                <div className="loading-state-title">Retrieving Clause Evidence &amp; Gazettes...</div>
                <div className="loading-state-sub">Searching ChromaDB vector index and BM25 corpus</div>
              </div>
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} style={{ height: 1 }} />
    </div>
  );
}
