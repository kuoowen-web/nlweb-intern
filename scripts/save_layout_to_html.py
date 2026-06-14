#!/usr/bin/env python3
"""
Save layout data to architecture.html

This script safely updates the graphData in architecture.html without breaking the file.
It handles newline escaping correctly to avoid JavaScript syntax errors.

Usage:
    python save_layout_to_html.py

Prerequisites:
    - layout-data-for-save.json must exist in the current directory
    - static/architecture.html must exist
"""

import json
import re
import os
import sys

# Fix encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def escape_newlines_in_json_strings(json_str):
    """
    Manually escape newlines within JSON string values.

    This is necessary because when JSON contains actual newline characters
    in string values, embedding it in HTML <script> tags causes JavaScript
    syntax errors. We need to convert actual newlines to \\n escape sequences.

    Args:
        json_str: JSON string that may contain unescaped newlines

    Returns:
        JSON string with newlines properly escaped
    """
    result = []
    in_string = False
    escape_next = False

    for i, char in enumerate(json_str):
        prev_char = json_str[i-1] if i > 0 else ''

        # Track if we're inside a string (ignoring escaped quotes)
        if char == '"' and prev_char != '\\':
            in_string = not in_string
            result.append(char)
        # If we're in a string and encounter a newline, escape it
        elif in_string and char == '\n':
            result.append('\\n')
        else:
            result.append(char)

    return ''.join(result)


def load_layout_data(json_path):
    """Load layout data from JSON file."""
    if not os.path.exists(json_path):
        print(f"❌ Error: {json_path} not found!")
        print(f"\nPlease:")
        print(f"1. Open architecture.html in browser")
        print(f"2. Enter edit mode")
        print(f"3. Click '💾 儲存佈局到 HTML' button")
        print(f"4. This will download {json_path}")
        print(f"5. Run this script again")
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def update_html_file(html_path, layout_data):
    """Update the graphData in HTML file with new layout data."""

    # Read original HTML
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the graphData block
    pattern = r'(let graphData = null;.*?graphData = )(\{.*?\});'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        print("❌ Error: Could not find graphData in HTML file!")
        sys.exit(1)

    prefix = match.group(1)

    # Convert layout data to JSON string
    json_str = json.dumps(layout_data, indent=2, ensure_ascii=False)

    # Escape newlines in string values
    json_str_escaped = escape_newlines_in_json_strings(json_str)

    # Rebuild HTML content
    new_graphdata_block = prefix + json_str_escaped + ';'
    new_content = content[:match.start()] + new_graphdata_block + content[match.end():]

    # Create backup
    backup_path = html_path + '.backup-before-save'
    with open(backup_path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print(f"✅ Backup created: {backup_path}")

    # Write updated HTML
    with open(html_path, 'w', encoding='utf-8', newline='') as f:
        f.write(new_content)

    return True


def main():
    """Main execution function."""

    print("=" * 60)
    print("  Save Layout to architecture.html")
    print("=" * 60)
    print()

    # Get script directory and project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)  # Go up one level from scripts/

    # Paths
    json_path = os.path.join(project_root, 'static', 'layout-data-for-save.json')
    html_path = os.path.join(project_root, 'static', 'architecture.html')

    print(f"📂 Project root: {project_root}")
    print(f"📂 Looking for JSON: {json_path}")
    print(f"📂 Looking for HTML: {html_path}")
    print()

    # Check if HTML exists
    if not os.path.exists(html_path):
        print(f"❌ Error: {html_path} not found!")
        sys.exit(1)

    # Load layout data
    print(f"📂 Loading layout data from {json_path}...")
    layout_data = load_layout_data(json_path)

    node_count = len(layout_data.get('nodes', []))
    edge_count = len(layout_data.get('edges', []))
    box_count = len(layout_data.get('moduleBoxes', []))

    print(f"   Nodes: {node_count}")
    print(f"   Edges: {edge_count}")
    print(f"   Module Boxes: {box_count}")
    print()

    # Confirm
    response = input("⚠️  This will modify architecture.html. Continue? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("❌ Cancelled by user.")
        sys.exit(0)

    # Update HTML
    print(f"\n💾 Updating {html_path}...")
    update_html_file(html_path, layout_data)

    print(f"✅ Successfully updated {html_path}!")
    print()
    print("Next steps:")
    print(f"1. Open {html_path} in browser to verify")
    print(f"2. If OK, you can delete {json_path}")
    print(f"3. Backup file created: {html_path}.backup-before-save")
    print()
    print("=" * 60)


if __name__ == '__main__':
    main()
