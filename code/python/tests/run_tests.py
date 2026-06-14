#!/usr/bin/env python
"""Simple test runner script."""
import sys
import os

# Add code/python to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

if __name__ == "__main__":
    test_dir = os.path.dirname(os.path.abspath(__file__))

    # Run specific test file if provided, otherwise run all
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        pytest.main([os.path.join(test_dir, test_file), "-v", "--tb=short"])
    else:
        pytest.main([test_dir, "-v", "--tb=short"])
