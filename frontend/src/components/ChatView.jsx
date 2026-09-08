import React, { useState, useRef, useEffect } from 'react';
import Sidebar from './Sidebar';
import MessageList from './MessageList';
import ChatInput from './ChatInput';
import { Menu, RotateCcw, ShieldCheck } from 'lucide-react';
import { streamChatQuery } from '../utils/api';

export default function ChatView({ initialQuery, onClearInitialQuery, healthStatus }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [streamingMessage, setStreamingMessage] = useState(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  const abortControllerRef = useRef(null);

  // If initialQuery is passed (e.g. from Landing page search), trigger it
  useEffect(() => {
    if (initialQuery && initialQuery.trim().length >= 3) {
      handleSend(initialQuery.trim());
      onClearInitialQuery?.();
    }
  }, [initialQuery]);

  const handleSend = async (customQuery) => {
    const queryToSend = (customQuery || input).trim();
    if (!queryToSend || isLoading) return;

    if (!customQuery) {
      setInput('');
    }

    // Add user message to history
    const userMsg = { role: 'user', content: queryToSend };
    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    // Prepare streaming container
    let currentBotMsg = {
      role: 'assistant',
      content: '',
      sources: [],
      route: '',
      confidence: 0,
      low_confidence: false,
      stripped_is: [],
    };
    setStreamingMessage(currentBotMsg);

    abortControllerRef.current = new AbortController();

    try {
      await streamChatQuery({
        query: queryToSend,
        history: messages,
        onMeta: (meta) => {
          currentBotMsg = {
            ...currentBotMsg,
            sources: meta.sources || [],
            route: meta.route || '',
            confidence: meta.confidence || 0,
            low_confidence: meta.low_confidence || false,
            used_nim: meta.used_nim,
          };
          setStreamingMessage({ ...currentBotMsg });
        },
        onToken: (token) => {
          currentBotMsg.content += token;
          setStreamingMessage({ ...currentBotMsg });
        },
        onStripped: (items) => {
          currentBotMsg.stripped_is = items;
          setStreamingMessage({ ...currentBotMsg });
        },
        onDone: () => {
          setMessages((prev) => [...prev, { ...currentBotMsg }]);
          setStreamingMessage(null);
          setIsLoading(false);
        },
        onError: (err) => {
          console.error('Stream error:', err);
          if (currentBotMsg.content) {
            setMessages((prev) => [...prev, { ...currentBotMsg }]);
          } else {
            setMessages((prev) => [
              ...prev,
              {
                role: 'assistant',
                content: `⚠️ ${err.message || 'Unable to fetch response from BIS backend.'}`,
                sources: [],
              },
            ]);
          }
          setStreamingMessage(null);
          setIsLoading(false);
        },
        signal: abortControllerRef.current.signal,
      });
    } catch (err) {
      if (err.name === 'AbortError') {
        if (currentBotMsg.content) {
          setMessages((prev) => [...prev, { ...currentBotMsg }]);
        }
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: `⚠️ Error connecting to server: ${err.message}`,
            sources: [],
          },
        ]);
      }
      setStreamingMessage(null);
      setIsLoading(false);
    } finally {
      abortControllerRef.current = null;
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setIsLoading(false);
    }
  };

  const handleResetChat = () => {
    handleStop();
    setMessages([]);
    setStreamingMessage(null);
  };

  return (
    <div className="chat-layout">
      <Sidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        onNewChat={handleResetChat}
        onSelectPrompt={(p) => handleSend(p)}
        healthStatus={healthStatus}
      />

      <main className="chat-main">
        {/* Top Action Bar */}
        <div className="chat-topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="sidebar-toggle-btn"
              onClick={() => setIsSidebarOpen(true)}
              aria-label="Open sidebar"
            >
              <Menu size={18} />
            </button>
            <span className="topbar-topic">Official BIS Standards &amp; QCO Assistant</span>
          </div>

          <div className="topbar-right">
            <button
              type="button"
              className="topbar-action-btn"
              onClick={handleResetChat}
              title="Clear all messages"
            >
              <RotateCcw size={14} />
              <span>Clear Chat</span>
            </button>
          </div>
        </div>

        {/* Messages Feed */}
        <MessageList
          messages={messages}
          isLoading={isLoading}
          streamingMessage={streamingMessage}
          onSelectPrompt={(p) => handleSend(p)}
        />

        {/* Bottom Input Area */}
        <ChatInput
          input={input}
          setInput={setInput}
          onSend={() => handleSend()}
          isLoading={isLoading}
          onStop={handleStop}
        />
      </main>
    </div>
  );
}
