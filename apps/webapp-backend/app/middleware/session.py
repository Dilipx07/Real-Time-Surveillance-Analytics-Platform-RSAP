"""Dual-token enforcement is exposed as the verify_dual_token dependency.

Route-level enforcement keeps the login and desktop license-verification contracts
explicit while ensuring all other HTTP API routes require both credentials.
"""

from app.dependencies import verify_dual_token

__all__ = ["verify_dual_token"]
