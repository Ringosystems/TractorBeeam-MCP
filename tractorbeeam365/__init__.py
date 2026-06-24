"""TractorBeeam365 MCP — an independent Model Context Protocol server for
Veeam Backup for Microsoft 365 (VB365).

Not affiliated with, endorsed by, or supported by Veeam Software.
"Veeam" is a registered trademark of Veeam Software. The "Beeam" spelling is a
deliberate, distinct wordplay and not the trademark.

The package keeps the original read-only reviewer as the always-on, safe default
and layers optional, explicitly-gated capabilities on top:
  * graph.py     — Microsoft 365 (Graph) gap-analysis: what's live vs protected.
  * cloud.py     — Backblaze B2 / S3 object-lock cross-check at the bucket.
  * analysis.py  — pure trend/anomaly/coverage math over the read-only data.
  * actions.py   — opt-in, confirm-gated, audited operational actions.
  * restore.py   — opt-in, confirm-gated, audited restore orchestration.
"""

__version__ = "2.1.2"
__all__ = ["vb365", "analysis", "graph", "cloud", "actions", "restore"]
