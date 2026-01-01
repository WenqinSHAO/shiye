#!/usr/bin/env python3
"""Test script to demonstrate debug retrieval logging."""

import os
os.environ['SHIYE_DEBUG_RETRIEVAL'] = 'true'

from workspace import Workspace
from retrieval import SearchRequest

def main():
    """Run a test search with debug logging enabled."""
    print("Initializing workspace...")
    workspace = Workspace()
    
    # Create a simple search request
    request = SearchRequest(
        query="kubernetes container",
        top_k=5,
        enable_rerank=True,
        enable_time_boost=True,
        enable_exact_boost=True
    )
    
    print("\nExecuting search with debug logging...\n")
    results = workspace.search(request)
    
    print(f"\nSearch completed. Found {len(results)} results.")

if __name__ == "__main__":
    main()
