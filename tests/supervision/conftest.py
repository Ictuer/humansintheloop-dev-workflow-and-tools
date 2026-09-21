"""Shared setup for supervision tests."""

import os
import sys

# Reuse the implement test doubles (FakeClaudeRunner, ...), as tests/setup-cmd and tests/improve do.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "implement"))
