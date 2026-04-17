#!/usr/bin/env python3
"""Test skill script."""
import sys

args = sys.argv[1:]
if args:
    print(f"Test skill executed with args: {' '.join(args)}")
else:
    print("Test skill executed successfully")
