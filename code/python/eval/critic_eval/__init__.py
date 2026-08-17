"""
Critic auto-eval harness.

Dynamically evaluates the *quality* of the Critic agent's judgments (not the
static wording of its prompt). Feeds a fixed set of (draft, sources) cases
through the real critic, then runs three adversarial LLM judges to find issues
the critic missed.
"""
