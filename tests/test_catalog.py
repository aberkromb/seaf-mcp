#!/usr/bin/env python3
"""Test script to validate endpoint catalog loading and tool generation."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from seaf_mcp.endpoint_catalog import load_catalog
from seaf_mcp.tool_factory import filter_and_create_tools

def main():
    print("=" * 60)
    print("SEAF MCP Server - Catalog Validation")
    print("=" * 60)
    print()
    
    # Load catalog
    catalog_path = os.path.join(os.path.dirname(__file__), '..', 'endpoints.json')
    print(f"Loading catalog from: {catalog_path}")
    
    try:
        catalog = load_catalog(catalog_path)
        print(f"✓ Catalog loaded successfully")
        print(f"  Version: {catalog.version}")
        print(f"  Base URL: {catalog.baseUrl}")
        print(f"  Total endpoints: {len(catalog.endpoints)}")
        print()
    except Exception as e:
        print(f"✗ Failed to load catalog: {e}")
        return 1
    
    # Filter and create tools
    print("Filtering endpoints and creating tools...")
    try:
        tools, skipped, tool_map = filter_and_create_tools(catalog.endpoints)
        print(f"✓ Tool generation completed")
        print()
    except Exception as e:
        print(f"✗ Failed to create tools: {e}")
        return 1
    
    # Summary
    print("-" * 60)
    print("SUMMARY")
    print("-" * 60)
    print(f"Total endpoints:     {len(catalog.endpoints)}")
    print(f"✓ Tools registered:  {len(tools)}")
    print(f"✗ Endpoints skipped: {len(skipped)}")
    print()
    
    # Show registered tools
    if tools:
        print("Registered tools:")
        for tool in tools:
            print(f"  - {tool['name']}")
        print()
    
    # Show skipped endpoints with reasons
    if skipped:
        print("Skipped endpoints:")
        for endpoint_id, reason in skipped:
            print(f"  - {endpoint_id}")
            print(f"    Reason: {reason}")
        print()
    
    print("=" * 60)
    print("Validation completed successfully!")
    print("=" * 60)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
