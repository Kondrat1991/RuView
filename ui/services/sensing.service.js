/**
 * Sensing WebSocket Service
 *
 * Manages the connection to the Python sensing WebSocket server
 * (ws://localhost:8765) and provides a callback-based API for the UI.
 *
 * Stays in reconnecting state when server is unreachable — no simulated data
 * is emitted, so the UI always shows real ESP32 data or a clear offline state.
 */

// Derive WebSocket URL from the page origin so it works on any port.
// The /ws/sensing endpoint is available on the same HTTP port (3000).
const _wsProto = (typeof window !== 'undefined' && window.location.protocol === 'https:') ? 'wss:' : 'ws:';
const _wsHost  = (typeof window !== 'undefined' && window.location.host) ? window.location.host : 'localhost:3000';
const SENSING_WS_URL = `${_wsProto}//${_wsHost}/ws/sensing`;
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const MAX_RECONNECT_ATTEMPTS = 20;

class SensingService {
  constructor() {
    /** @type {WebSocket|null} */
    this._ws = null;
    this._listeners = new Set();
    this._stateListeners = new Set();
    this._reconnectAttempt = 0;
    this._reconnectTimer = null;
    // Connection state: disconnected | connecting | connected | reconnecting
    this._state = 'disconnected';
    // Data-source label exposed to the UI:
    //   "live"         — real ESP32 hardware connected
    //   "reconnecting" — WebSocket disconnected, retrying
    this._dataSource = 'reconnecting';
    // The raw source string from the server (e.g. "esp32")
    this._serverSource = null;
    this._lastMessage = null;

    // Ring buffer of recent RSSI values for sparkline
    this._rssiHistory = [];
    this._maxHistory = 60;
  }

  // ---- Public API --------------------------------------------------------

  /** Start the service (connect or simulate). */
  start() {
    this._connect();
  }

  /** Stop the service entirely. */
  stop() {
    this._clearTimers();
    if (this._ws) {
      this._ws.close(1000, 'client stop');
      this._ws = null;
    }
    this._setState('disconnected');
  }

  /** Register a callback for sensing data updates. Returns unsubscribe fn. */
  onData(callback) {
    this._listeners.add(callback);
    // Immediately push last known data if available
    if (this._lastMessage) callback(this._lastMessage);
    return () => this._listeners.delete(callback);
  }

  /** Register a callback for connection state changes. Returns unsubscribe fn. */
  onStateChange(callback) {
    this._stateListeners.add(callback);
    callback(this._state);
    return () => this._stateListeners.delete(callback);
  }

  /** Get the RSSI sparkline history (array of floats). */
  getRssiHistory() {
    return [...this._rssiHistory];
  }

  /** Get per-node RSSI history (object keyed by node_id). */
  getPerNodeRssiHistory() {
    return { ...(this._perNodeRssiHistory || {}) };
  }

  /** Current connection state. */
  get state() {
    return this._state;
  }

  /**
   * Current data source label.
   * "live"         — frames are arriving from the real ESP32 over WebSocket
   * "reconnecting" — WebSocket disconnected; actively retrying, no frames emitted
   * "simulated"    — max reconnect attempts exhausted; emitting synthetic frames
   */
  get dataSource() {
    return this._dataSource;
  }

  // ---- Connection --------------------------------------------------------

  _connect() {
    if (this._ws && this._ws.readyState <= WebSocket.OPEN) return;

    this._setState('connecting');

    try {
      this._ws = new WebSocket(SENSING_WS_URL);
    } catch (err) {
      console.warn('[Sensing] WebSocket constructor failed:', err.message);
      this._fallbackToSimulation();
      return;
    }

    this._ws.onopen = () => {
      console.info('[Sensing] Connected to', SENSING_WS_URL);
      this._reconnectAttempt = 0;
      this._stopSimulation();
      this._setState('connected');
      // Don't assume "live" yet — wait for first frame's source field.
      // Fetch server status to determine actual data source immediately.
      this._detectServerSource();
    };

    this._ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        this._handleData(data);
      } catch (e) {
        console.warn('[Sensing] Invalid message:', e.message);
      }
    };

    this._ws.onerror = () => {
      // onerror is always followed by onclose, so we handle reconnect there
    };

    this._ws.onclose = (evt) => {
      console.info('[Sensing] Connection closed (code=%d)', evt.code);
      this._ws = null;
      if (evt.code !== 1000) {
        this._scheduleReconnect();
      } else {
        this._setState('disconnected');
        this._setDataSource('reconnecting');
      }
    };
  }

  _scheduleReconnect() {
    const delay = RECONNECT_DELAYS[Math.min(this._reconnectAttempt, RECONNECT_DELAYS.length - 1)];
    this._reconnectAttempt++;
    console.info('[Sensing] Reconnecting in %dms (attempt %d)', delay, this._reconnectAttempt);

    this._setState('reconnecting');
    this._setDataSource('reconnecting');

    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._connect();
    }, delay);
  }

  // ---- Server source detection -------------------------------------------

  /**
   * Fetch `/api/v1/status` to find out if the server is using real
   * hardware or simulation. Called once on WebSocket open.
   */
  async _detectServerSource() {
    try {
      const resp = await fetch('/api/v1/status');
      if (resp.ok) {
        const json = await resp.json();
        this._applyServerSource(json.source);
      } else {
        // Can't reach status endpoint — assume live until first frame tells us
        this._setDataSource('live');
      }
    } catch {
      this._setDataSource('live');
    }
  }

  /**
   * Map a raw server source string to the UI data-source label.
   */
  _applyServerSource(rawSource) {
    this._serverSource = rawSource;
    if (rawSource === 'esp32' || rawSource === 'wifi' || rawSource === 'live') {
      this._setDataSource('live');
    } else {
      this._setDataSource('reconnecting');
    }
  }

  /** @return {string|null} Raw server source (e.g. "esp32", "simulated") */
  get serverSource() {
    return this._serverSource;
  }

  // ---- Data handling -----------------------------------------------------

  _handleData(data) {
    this._lastMessage = data;

    // Track the server's source field from each frame so the UI
    // can react if the server switches between esp32 ↔ simulated at runtime.
    if (data.source && this._state === 'connected') {
      const raw = data.source;
      if (raw !== this._serverSource) {
        this._applyServerSource(raw);
      }
    }

    // Update RSSI history for sparkline
    if (data.features && data.features.mean_rssi != null) {
      this._rssiHistory.push(data.features.mean_rssi);
      if (this._rssiHistory.length > this._maxHistory) {
        this._rssiHistory.shift();
      }
    }

    // Per-node RSSI tracking
    if (!this._perNodeRssiHistory) this._perNodeRssiHistory = {};
    if (data.node_features) {
      for (const nf of data.node_features) {
        if (!this._perNodeRssiHistory[nf.node_id]) {
          this._perNodeRssiHistory[nf.node_id] = [];
        }
        this._perNodeRssiHistory[nf.node_id].push(nf.rssi_dbm);
        if (this._perNodeRssiHistory[nf.node_id].length > this._maxHistory) {
          this._perNodeRssiHistory[nf.node_id].shift();
        }
      }
    }

    // Notify all listeners
    for (const cb of this._listeners) {
      try {
        cb(data);
      } catch (e) {
        console.error('[Sensing] Listener error:', e);
      }
    }
  }

  // ---- State management --------------------------------------------------

  _setState(newState) {
    if (newState === this._state) return;
    this._state = newState;
    for (const cb of this._stateListeners) {
      try { cb(newState); } catch (e) { /* ignore */ }
    }
  }

  /**
   * Update the dataSource label and notify state listeners so the UI can
   * react without needing a separate subscription.
   * @param {'live'|'reconnecting'} source
   */
  _setDataSource(source) {
    if (source === this._dataSource) return;
    this._dataSource = source;
    // Re-use the same state-listener channel — listeners receive the
    // connection state but can read dataSource via service.dataSource.
    for (const cb of this._stateListeners) {
      try { cb(this._state); } catch (e) { /* ignore */ }
    }
  }

  _clearTimers() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }
}

// Singleton
export const sensingService = new SensingService();
