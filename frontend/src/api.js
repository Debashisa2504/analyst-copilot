/**
 * frontend/src/api.js
 * Thin fetch wrapper around the FastAPI backend.
 *
 * askQuestionStream() uses SSE (EventSource-style fetch/ReadableStream)
 * so the UI can progressively show retrieval → draft → verify → answer.
 */
const BASE = import.meta.env.VITE_API_BASE || "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers:
      options.body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

/**
 * SSE streaming question answering.
 *
 * Calls POST /answer/stream and feeds each SSE event to the provided callback:
 *   onEvent(eventType, data)
 *
 * Event types: 'retrieval' | 'draft' | 'verify' | 'answer' | 'error'
 *
 * Returns a promise that resolves when the stream closes.
 */
export async function askQuestionStream(question, docName, onEvent, topK = 10) {
  const res = await fetch(`${BASE}/answer/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, doc_name: docName, top_k: topK }),
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE messages are separated by double newlines
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      if (!part.trim()) continue;
      let eventType = "message";
      let dataLine = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event: ")) eventType = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLine = line.slice(6).trim();
      }
      if (dataLine) {
        try {
          onEvent(eventType, JSON.parse(dataLine));
        } catch (_) {
          onEvent(eventType, { raw: dataLine });
        }
      }
    }
  }
}

export const api = {
  health: () => request("/health"),
  listFilings: () => request("/filings"),
  uploadFilings: (files) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request("/upload", { method: "POST", body: form });
  },
  // Non-streaming fallback (for eval harness / tests)
  askQuestion: (question, docName, topK = 10) =>
    request("/answer", {
      method: "POST",
      body: JSON.stringify({ question, doc_name: docName, top_k: topK }),
    }),
  getIntelligence: (docName) =>
    request(`/filings/${encodeURIComponent(docName)}/intelligence`),
  sendFeedback: (chunkIds, verdict) =>
    request("/feedback", {
      method: "POST",
      body: JSON.stringify({ chunk_ids: chunkIds, verdict }),
    }),
};
