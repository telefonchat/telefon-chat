#!/usr/bin/env python3
"""Bot entry point — loads config and runs framework."""
import sys, os
sys.path.insert(0, "/opt/bots/framework")
from bot_framework import run_bot

if __name__ == "__main__":
    config = os.path.join(os.path.dirname(__file__), "config.json")
    run_bot(config)
