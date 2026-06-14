/**
 * Analytics Tracker for NLWeb
 *
 * Tracks user interactions with search results for ML training data collection.
 *
 * Features:
 * - Click tracking on result links
 * - Dwell time measurement
 * - Scroll depth tracking
 * - Privacy-conscious (no PII collection)
 * - Sends data via WebSocket to backend
 */

export class AnalyticsTracker {
  constructor(websocketConnection = null) {
    this.ws = websocketConnection;
    this.currentQuery = null;
    this.currentQueryId = null;
    this.resultInteractions = new Map(); // url -> interaction data
    this.pageVisibleTime = new Map(); // url -> start timestamp
    this.scrollDepths = new Map(); // url -> max scroll depth

    // Intersection Observer for visibility tracking
    this.visibilityObserver = null;

    this.init();
  }

  /**
   * Initialize the tracker
   */
  init() {
    // Set up intersection observer for result visibility
    this.setupVisibilityObserver();

    // Set up scroll depth tracking
    this.setupScrollTracking();

    // Set up page visibility API (for dwell time)
    this.setupPageVisibility();
  }

  /**
   * Set the WebSocket connection for sending events
   */
  setWebSocket(ws) {
    this.ws = ws;
  }

  /**
   * Start tracking a new query
   */
  startQuery(queryId, queryText) {
    this.currentQueryId = queryId;
    this.currentQuery = queryText;
    this.resultInteractions.clear();
    this.pageVisibleTime.clear();
    this.scrollDepths.clear();

    console.log('[Analytics] Started tracking query:', queryId);
  }

  /**
   * Track a result being displayed
   */
  trackResultDisplayed(url, position, metadata = {}) {
    if (!this.currentQueryId) return;

    // Initialize interaction data for this result
    if (!this.resultInteractions.has(url)) {
      this.resultInteractions.set(url, {
        url: url,
        position: position,
        displayed_at: Date.now(),
        clicked: false,
        click_timestamp: null,
        visible_duration_ms: 0,
        scroll_depth_percent: 0,
        metadata: metadata
      });
    }

    // Send display event
    this.sendEvent('result_displayed', {
      query_id: this.currentQueryId,
      doc_url: url,
      result_position: position,
      interaction_type: 'display',
      ...metadata
    });
  }

  /**
   * Track a click on a result
   */
  trackClick(url, position, event = null) {
    if (!this.currentQueryId) return;

    const interaction = this.resultInteractions.get(url);
    if (interaction) {
      interaction.clicked = true;
      interaction.click_timestamp = Date.now();
    }

    console.log('[Analytics] Click tracked:', url, 'position:', position);

    // Send click event
    this.sendEvent('result_clicked', {
      query_id: this.currentQueryId,
      doc_url: url,
      result_position: position,
      interaction_type: 'click',
      clicked: true,
      client_user_agent: navigator.userAgent,
      client_ip_hash: this.hashIP() // Privacy-conscious hashing
    });

    // Start tracking dwell time for this result
    this.pageVisibleTime.set(url, Date.now());
  }

  /**
   * Track scroll depth on a result page
   * (Called periodically or on page unload)
   */
  trackScrollDepth(url, scrollDepth) {
    if (!this.currentQueryId) return;

    const currentMax = this.scrollDepths.get(url) || 0;
    if (scrollDepth > currentMax) {
      this.scrollDepths.set(url, scrollDepth);

      // Update interaction data
      const interaction = this.resultInteractions.get(url);
      if (interaction) {
        interaction.scroll_depth_percent = scrollDepth;
      }
    }
  }

  /**
   * Track dwell time when user returns or leaves page
   */
  trackDwellTime(url) {
    if (!this.currentQueryId) return;

    const startTime = this.pageVisibleTime.get(url);
    if (startTime) {
      const dwellTime = Date.now() - startTime;

      // Update interaction data
      const interaction = this.resultInteractions.get(url);
      if (interaction) {
        interaction.visible_duration_ms += dwellTime;
      }

      // Send dwell time event
      this.sendEvent('dwell_time', {
        query_id: this.currentQueryId,
        doc_url: url,
        interaction_type: 'dwell',
        dwell_time_ms: dwellTime,
        scroll_depth_percent: this.scrollDepths.get(url) || 0
      });

      console.log('[Analytics] Dwell time tracked:', url, dwellTime + 'ms');

      // Clear the start time
      this.pageVisibleTime.delete(url);
    }
  }

  /**
   * Set up Intersection Observer for visibility tracking
   */
  setupVisibilityObserver() {
    // Observe when results enter/leave viewport
    this.visibilityObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const url = entry.target.dataset.url;
        const position = parseInt(entry.target.dataset.position) || 0;

        if (entry.isIntersecting) {
          // Result became visible
          this.pageVisibleTime.set(url, Date.now());
        } else {
          // Result left viewport
          if (this.pageVisibleTime.has(url)) {
            this.trackDwellTime(url);
          }
        }
      });
    }, {
      threshold: [0.5] // Consider visible when 50% is in viewport
    });
  }

  /**
   * Observe a result element for visibility
   */
  observeResult(element) {
    if (this.visibilityObserver && element) {
      this.visibilityObserver.observe(element);
    }
  }

  /**
   * Set up scroll depth tracking
   */
  setupScrollTracking() {
    let ticking = false;

    window.addEventListener('scroll', () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          const scrollDepth = this.calculateScrollDepth();

          // Track scroll depth for currently visible results
          this.pageVisibleTime.forEach((startTime, url) => {
            this.trackScrollDepth(url, scrollDepth);
          });

          ticking = false;
        });

        ticking = true;
      }
    });
  }

  /**
   * Calculate current scroll depth as percentage
   */
  calculateScrollDepth() {
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;

    const maxScroll = documentHeight - windowHeight;
    const scrollPercent = maxScroll > 0 ? (scrollTop / maxScroll) * 100 : 0;

    return Math.min(100, Math.round(scrollPercent));
  }

  /**
   * Set up page visibility API for accurate dwell time
   */
  setupPageVisibility() {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        // Page became hidden - track dwell time for all visible results
        this.pageVisibleTime.forEach((startTime, url) => {
          this.trackDwellTime(url);
        });
      } else {
        // Page became visible again - restart timers
        this.pageVisibleTime.forEach((startTime, url) => {
          this.pageVisibleTime.set(url, Date.now());
        });
      }
    });

    // Also track on page unload
    window.addEventListener('beforeunload', () => {
      this.pageVisibleTime.forEach((startTime, url) => {
        this.trackDwellTime(url);
      });
    });
  }

  /**
   * Send analytics event to backend
   */
  sendEvent(eventType, data) {
    if (!this.ws || !this.ws.readyState || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[Analytics] WebSocket not available, event not sent:', eventType);
      return;
    }

    const event = {
      type: 'analytics_event',
      event_type: eventType,
      timestamp: Date.now(),
      data: data
    };

    try {
      this.ws.send(JSON.stringify(event));
      console.log('[Analytics] Event sent:', eventType, data.doc_url);
    } catch (error) {
      console.error('[Analytics] Error sending event:', error);
    }
  }

  /**
   * Privacy-conscious IP hashing
   * (Frontend can't actually access IP, this is a placeholder)
   */
  hashIP() {
    // In practice, IP hashing should be done server-side
    // Frontend can send a session ID instead
    return 'client-side-hash';
  }

  /**
   * Get summary of interactions for current query
   */
  getInteractionSummary() {
    const summary = {
      query_id: this.currentQueryId,
      query_text: this.currentQuery,
      total_results: this.resultInteractions.size,
      clicked_results: 0,
      avg_visible_duration: 0,
      avg_scroll_depth: 0
    };

    let totalVisible = 0;
    let totalScroll = 0;

    this.resultInteractions.forEach(interaction => {
      if (interaction.clicked) {
        summary.clicked_results++;
      }
      totalVisible += interaction.visible_duration_ms;
      totalScroll += interaction.scroll_depth_percent;
    });

    if (this.resultInteractions.size > 0) {
      summary.avg_visible_duration = totalVisible / this.resultInteractions.size;
      summary.avg_scroll_depth = totalScroll / this.resultInteractions.size;
    }

    return summary;
  }

  /**
   * Export interaction data (for debugging)
   */
  exportData() {
    return {
      current_query: this.currentQuery,
      current_query_id: this.currentQueryId,
      interactions: Array.from(this.resultInteractions.values()),
      summary: this.getInteractionSummary()
    };
  }
}

// Global singleton instance
let globalTracker = null;

/**
 * Get or create the global analytics tracker
 */
export function getAnalyticsTracker(ws = null) {
  if (!globalTracker) {
    globalTracker = new AnalyticsTracker(ws);
  } else if (ws) {
    globalTracker.setWebSocket(ws);
  }
  return globalTracker;
}

/**
 * Helper function to attach click tracking to a result element
 */
export function attachClickTracking(element, url, position, tracker = null) {
  if (!tracker) {
    tracker = getAnalyticsTracker();
  }

  // Find all links in the element
  const links = element.querySelectorAll('a[href]');

  links.forEach(link => {
    link.addEventListener('click', (event) => {
      tracker.trackClick(url, position, event);
    });
  });

  // Set data attributes for visibility tracking
  element.dataset.url = url;
  element.dataset.position = position;

  // Observe element for visibility
  tracker.observeResult(element);
}
