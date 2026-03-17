export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) {
    return configured.replace(/\/$/, '')
  }

  if (typeof window === 'undefined') {
    return 'http://localhost:8000'
  }

  return `${window.location.protocol}//${window.location.hostname}:8000`
}

export function getWsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL
  if (configured) {
    return configured
  }

  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/ws/chat'
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.hostname}:8000/ws/chat`
}
