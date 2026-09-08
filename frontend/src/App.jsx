import React, { useState, useEffect } from 'react';
import Navbar from './components/Navbar';
import LandingPage from './components/LandingPage';
import ChatView from './components/ChatView';
import { checkBackendHealth } from './utils/api';
import './App.css';

export default function App() {
  const [activeTab, setActiveTab] = useState('landing');
  const [healthStatus, setHealthStatus] = useState(null);
  const [initialChatQuery, setInitialChatQuery] = useState('');

  // Initial health check + periodic poll
  useEffect(() => {
    let isMounted = true;

    const fetchHealth = async () => {
      const data = await checkBackendHealth();
      if (isMounted) {
        setHealthStatus(data);
      }
    };

    fetchHealth();
    const interval = setInterval(fetchHealth, 25000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleStartChatWithQuery = (query) => {
    if (query) {
      setInitialChatQuery(query);
    }
    setActiveTab('chat');
  };

  return (
    <div className={`app-container ${activeTab === 'chat' ? 'chat-mode' : 'landing-mode'}`}>
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />

      {activeTab === 'landing' ? (
        <LandingPage
          onStartChat={handleStartChatWithQuery}
          healthStatus={healthStatus}
        />
      ) : (
        <ChatView
          initialQuery={initialChatQuery}
          onClearInitialQuery={() => setInitialChatQuery('')}
          healthStatus={healthStatus}
        />
      )}
    </div>
  );
}
