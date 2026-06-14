/**
 * Analytics Tracker for NLWeb (SSE-Compatible Version)
 *
 * Tracks user interactions with search results for ML training data collection.
 * Uses HTTP POST for sending events (compatible with SSE-based frontends).
 *
 * Features:
 * - Click tracking on result links
 * - Dwell time measurement
 * - Scroll depth tracking
 * - Privacy-conscious (no PII collection)
 * - Sends data via HTTP POST to backend
 */

class AnalyticsTrackerSSE {
  constructor(apiEndpoint = '/api/analytics/event') {
    this.apiEndpoint = apiEndpoint;
    this.currentQuery = null;
    this.currentQueryId = null;
    this.resultInteractions = new Map(); // url -> interaction data
    this.pageVisibleTime = new Map(); // url -> start timestamp
    this.scrollDepths = new Map(); // url -> max scroll depth

    // Intersection Observer for visibility tracking
    this.visibilityObserver = null;

    // Batch events to reduce HTTP requests
    this.eventQueue = [];
    this.flushInterval = 5000; // Flush every 5 seconds
    this.flushTimer = null;

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

    // Start periodic event flushing
    this.startEventFlushing();

    console.log('[Analytics-SSE] Tracker initialized');
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

    console.log('[Analytics-SSE] Started tracking query:', queryId);

    // Send query start event
    this.sendEventImmediate('query_start', {
      query_id: queryId,
      query_text: queryText,
      timestamp: Date.now()
    });
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

    // Queue display event
    this.queueEvent('result_displayed', {
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

    console.log('[Analytics-SSE] Click tracked:', url, 'position:', position);

    // Send click event immediately (don't queue)
    this.sendEventImmediate('result_clicked', {
      query_id: this.currentQueryId,
      doc_url: url,
      result_position: position,
      interaction_type: 'click',
      clicked: true,
      client_user_agent: navigator.userAgent,
      client_ip_hash: 'client-side' // Server will hash actual IP
    });

    // Start tracking dwell time for this result
    this.pageVisibleTime.set(url, Date.now());
  }

  /**
   * Track scroll depth on a result page
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

      // Queue dwell time event
      this.queueEvent('dwell_time', {
        query_id: this.currentQueryId,
        doc_url: url,
        interaction_type: 'dwell',
        dwell_time_ms: dwellTime,
        scroll_depth_percent: this.scrollDepths.get(url) || 0
      });

      console.log('[Analytics-SSE] Dwell time tracked:', url, dwellTime + 'ms');

      // Clear the start time
      this.pageVisibleTime.delete(url);
    }
  }

  /**
   * Set up Intersection Observer for visibility tracking
   */
  setupVisibilityObserver() {
    this.visibilityObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const url = entry.target.dataset.analyticsUrl;
        const position = parseInt(entry.target.dataset.analyticsPosition) || 0;

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
      // Use sendBeacon to ensure events are sent before page unload
      if (this.eventQueue.length > 0) {
        const payload = JSON.stringify({ events: this.eventQueue });
        navigator.sendBeacon(this.apiEndpoint + '/batch', new Blob([payload], { type: 'application/json' }));
        this.eventQueue = [];
      }
    });
  }

  /**
   * Queue an event for batched sending
   */
  queueEvent(eventType, data) {
    this.eventQueue.push({
      event_type: eventType,
      timestamp: Date.now(),
      data: data
    });

    // If queue is large, flush immediately
    if (this.eventQueue.length >= 10) {
      this.flushEvents();
    }
  }

  /**
   * Send an event immediately (bypass queue)
   */
  async sendEventImmediate(eventType, data) {
    const event = {
      type: 'analytics_event',
      event_type: eventType,
      timestamp: Date.now(),
      data: data
    };

    try {
      const response = await fetch(this.apiEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'same-origin',
        body: JSON.stringify(event)
      });

      if (!response.ok) {
        console.error('[Analytics-SSE] Failed to send event:', eventType, response.statusText);
      } else {
        console.log('[Analytics-SSE] Event sent:', eventType);
      }
    } catch (error) {
      console.error('[Analytics-SSE] Error sending event:', error);
    }
  }

  /**
   * Start periodic event flushing
   */
  startEventFlushing() {
    this.flushTimer = setInterval(() => {
      this.flushEvents();
    }, this.flushInterval);
  }

  /**
   * Flush all queued events to backend
   */
  async flushEvents() {
    if (this.eventQueue.length === 0) return;

    const eventsToSend = [...this.eventQueue];
    this.eventQueue = [];

    try {
      const response = await fetch(this.apiEndpoint + '/batch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'same-origin',
        body: JSON.stringify({
          events: eventsToSend
        })
      });

      if (!response.ok) {
        console.error('[Analytics-SSE] Failed to flush events:', response.statusText);
        // Re-queue failed events
        this.eventQueue.push(...eventsToSend);
      } else {
        console.log(`[Analytics-SSE] Flushed ${eventsToSend.length} events`);
      }
    } catch (error) {
      console.error('[Analytics-SSE] Error flushing events:', error);
      // Re-queue failed events
      this.eventQueue.push(...eventsToSend);
    }
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
   * Shutdown tracker (cleanup)
   */
  shutdown() {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
    }
    this.flushEvents(); // Final flush
    console.log('[Analytics-SSE] Tracker shutdown');
  }
}

/**
 * Helper function to attach click tracking to a result element
 *
 * Usage:
 *   attachClickTrackingSSE(element, url, position, tracker);
 */
function attachClickTrackingSSE(element, url, position, tracker) {
  // Find all links in the element
  const links = element.querySelectorAll('a[href]');

  links.forEach(link => {
    link.addEventListener('click', (event) => {
      tracker.trackClick(url, position, event);
    });
  });

  // Set data attributes for visibility tracking
  element.dataset.analyticsUrl = url;
  element.dataset.analyticsPosition = position;

  // Observe element for visibility
  tracker.observeResult(element);
}

// Export for use in inline scripts (if using modules)
// Or make available globally
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnalyticsTrackerSSE, attachClickTrackingSSE };
} else {
  // Make available globally for browser <script> tags
  window.AnalyticsTrackerSSE = AnalyticsTrackerSSE;
  window.attachClickTrackingSSE = attachClickTrackingSSE;
}
