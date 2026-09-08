import React, { useRef, useEffect } from 'react';
import { ArrowUp, Square, CornerDownLeft } from 'lucide-react';

export default function ChatInput({
  input,
  setInput,
  onSend,
  isLoading,
  onStop,
}) {
  const textareaRef = useRef(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [input]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading && input.trim().length >= 3) {
        onSend();
      }
    }
  };

  const charLength = input.length;
  const isSendDisabled = !isLoading && input.trim().length < 3;

  return (
    <div className="chat-input-wrapper">
      <div className="input-container">
        <div className="input-box">
          <textarea
            ref={textareaRef}
            rows={1}
            className="chat-textarea"
            placeholder="Search Indian Standards, mandatory QCOs, CRS products, or certification clauses..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            maxLength={1000}
          />

          {isLoading ? (
            <button
              type="button"
              className="chat-send-btn stop"
              onClick={onStop}
              title="Halt query stream"
              aria-label="Stop generation"
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              className="chat-send-btn"
              onClick={onSend}
              disabled={isSendDisabled}
              title="Submit query (Enter)"
              aria-label="Submit search"
            >
              <ArrowUp size={16} strokeWidth={2.5} />
            </button>
          )}
        </div>

        <div className="input-footer">
          <div className="input-hint">
            <CornerDownLeft size={11} />
            <span>Enter to submit query · Shift+Enter for newline</span>
          </div>

          <div className="char-counter" style={{ color: charLength > 900 ? 'var(--danger)' : undefined }}>
            {charLength}/1000 characters
          </div>
        </div>
      </div>
    </div>
  );
}
