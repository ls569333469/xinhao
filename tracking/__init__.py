"""ABC tracking candidate package — activated only by explicit runtime control.

All modules here are offline-only. The ledger SQLite lives under the submission
package (or an explicitly gated path). Do not wire into task_service.
"""
__version__ = "codex-abc-tracking-20261019-v3"
ISOLATED = True
PRODUCTION_WIRED = False

SOURCE_VERSION = "codex-abc-tracking-20261019-v3"
A_PIPELINE_SOURCE_VERSION = "s2-r10-isolated"
