/**
 * API utility for connecting to FastAPI backend
 */

export const getApiBase = () => {
  // Check if environment variable is provided (e.g. on Vercel)
  if (import.meta.env?.VITE_API_URL) {
    return import.meta.env.VITE_API_URL.replace(/\/$/, '');
  }
  
  // When running locally on Vite dev server with proxy
  if (typeof window !== 'undefined') {
    // If running on port 3000 (Vite), backend proxy routes /chat and /health
    if (window.location.port === '3000') {
      return '';
    }
    // Fallback direct address
    return 'http://127.0.0.1:8000';
  }
  return 'http://127.0.0.1:8000';
};

export async function checkBackendHealth() {
  const base = getApiBase();
  try {
    const res = await fetch(`${base}/health`, {
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Backend health check failed:', err);
    return null;
  }
}

export async function streamChatQuery({
  query,
  history = [],
  onMeta,
  onToken,
  onStripped,
  onDone,
  onError,
  signal,
}) {
  const base = getApiBase();
  const url = `${base}/chat/stream`;

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query: query.trim(),
      history: history.slice(-10),
      use_nim: true,
    }),
    signal,
  });

  if (!response.ok) {
    let errorDetail = `Server error (HTTP ${response.status})`;
    try {
      const errorJson = await response.json();
      if (typeof errorJson.detail === 'string') {
        errorDetail = errorJson.detail;
      } else if (Array.isArray(errorJson.detail)) {
        errorDetail = errorJson.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
      }
    } catch {
      // ignore json parse error
    }
    throw new Error(errorDetail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // remainder

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data: ')) continue;
      const dataStr = trimmed.slice(6);
      if (!dataStr) continue;

      let event;
      try {
        event = JSON.parse(dataStr);
      } catch (e) {
        console.warn('SSE Parse error:', e, dataStr);
        continue;
      }

      if (event.type === 'meta') {
        onMeta?.(event);
      } else if (event.type === 'token') {
        onToken?.(event.text || '');
      } else if (event.type === 'stripped_is') {
        onStripped?.(event.items || []);
      } else if (event.type === 'done') {
        onDone?.(event);
      } else if (event.type === 'error') {
        onError?.(new Error(event.message || 'Streaming error'));
      }
    }
  }
}
