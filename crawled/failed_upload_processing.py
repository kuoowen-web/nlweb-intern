#!/usr/bin/env python3
"""
Failed Upload Processing Script

This script reads failed upload log files, classifies failures by reason,
and allows interactive retry of failed uploads by category.

Features:
- Read and parse failed_upload_*.txt files
- Classify failures by error type
- Display statistics by category
- Interactive retry by category
- Generate new failed log if retry fails

Usage:
  python failed_upload_processing.py <failed_log_file> <original_tsv_file>

Example:
  python failed_upload_processing.py failed_upload_20251203_120000.txt data.tsv

Author: Claude Code
Date: 2025-12-03
"""

import asyncio
import sys
import os
import json
import time
import uuid
import hashlib
import argparse
from datetime import datetime
from typing import Dict, List, Set
from collections import defaultdict

# Add project path to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(project_root, 'code', 'python'))

try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client import models
except ImportError:
    print("="*80)
    print("ERROR: Missing required dependency 'qdrant-client'")
    print("="*80)
    print("Please install it with:")
    print("  pip install qdrant-client")
    print("="*80)
    sys.exit(1)

try:
    import tldextract
except ImportError:
    print("="*80)
    print("ERROR: Missing required dependency 'tldextract'")
    print("="*80)
    print("Please install it with:")
    print("  pip install tldextract")
    print("="*80)
    sys.exit(1)

try:
    from core.config import CONFIG
    from core.embedding import get_embedding
except ImportError as e:
    print("="*80)
    print(f"ERROR: Cannot import NLWeb modules: {str(e)}")
    print("="*80)
    print("Make sure you're running this script from the NLWeb project directory")
    print("="*80)
    sys.exit(1)


def truncate_text(text: str, max_chars: int = 20000) -> str:
    """Truncate text to avoid token limits"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[截斷]"


def extract_site_from_url(url: str, override: str = None) -> str:
    """Extract site name from URL using tldextract"""
    if override:
        return override
    
    try:
        extracted = tldextract.extract(url)
        site = extracted.domain
        
        if not site:
            raise ValueError(f"Cannot extract domain from URL: {url}")
        
        return site
    
    except Exception as e:
        raise ValueError(f"Failed to parse URL '{url}': {str(e)}")


async def classify_failures(failed_items: List[Dict], tsv_file: str) -> Dict[str, List[Dict]]:
    """
    Classify failed URLs by attempting to determine failure reasons
    
    Args:
        failed_items: List of dicts with 'url' and optional 'reason' from log
        tsv_file: Original TSV file path
    
    Returns:
        Dictionary mapping error categories to lists of article data
    """
    print("\nAnalyzing failures...")
    
    # Load TSV data
    url_to_data = {}
    with open(tsv_file, 'r', encoding='utf-8') as f:
        first_line = True
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            parts = line.split('\t', 1)
            if len(parts) != 2:
                continue
            
            url_col = parts[0]
            json_col = parts[1]
            
            try:
                schema = json.loads(json_col)
                url_to_data[url_col] = schema
            except json.JSONDecodeError:
                if first_line:
                    first_line = False
                    continue
                # Invalid JSON
                url_to_data[url_col] = None
            
            first_line = False
    
    # Classify failures
    categories = defaultdict(list)
    
    for item in failed_items:
        url = item['url']
        logged_reason = item.get('reason', '')
        
        if url not in url_to_data:
            categories['missing_from_tsv'].append({
                'url': url,
                'reason': logged_reason or 'URL not found in original TSV file'
            })
            continue
        
        schema = url_to_data[url]
        
        if schema is None:
            categories['invalid_json'].append({
                'url': url,
                'schema': None,
                'reason': logged_reason or 'Invalid JSON in TSV file'
            })
            continue
        
        # Check for missing required fields
        if 'headline' not in schema or not schema['headline']:
            categories['missing_headline'].append({
                'url': url,
                'schema': schema,
                'reason': 'Missing or empty headline'
            })
            continue
        
        if 'articleBody' not in schema or not schema['articleBody']:
            categories['missing_body'].append({
                'url': url,
                'schema': schema,
                'reason': 'Missing or empty articleBody'
            })
            continue
        
        # Check article body length
        body_length = len(schema.get('articleBody', ''))
        if body_length > 50000:
            categories['too_long'].append({
                'url': url,
                'schema': schema,
                'reason': f'Article too long ({body_length} chars)',
                'body_length': body_length
            })
            continue
        
        # If no obvious issue, use logged reason or classify as "unknown"
        categories['unknown'].append({
            'url': url,
            'schema': schema,
            'reason': logged_reason or 'Unknown error (likely rate limit or network issue)'
        })
    
    return dict(categories)


async def retry_upload_batch(articles: List[Dict], site_override: str = None) -> tuple[int, List[Dict]]:
    """
    Retry uploading a batch of articles
    
    Args:
        articles: List of article dictionaries with 'url' and 'schema'
        site_override: Optional site name override
    
    Returns:
        Tuple of (successful_count, failed_items with reasons)
    """
    # Get Qdrant client
    try:
        endpoint_config = CONFIG.retrieval_endpoints[CONFIG.write_endpoint]
        client = AsyncQdrantClient(url=endpoint_config.api_endpoint, api_key=endpoint_config.api_key)
        collection_name = "nlweb_collection"
    except Exception as e:
        print(f"ERROR: Failed to initialize Qdrant client: {str(e)}")
        return 0, [{'url': a['url'], 'reason': f'Qdrant init error: {str(e)}'} for a in articles]
    
    successful = 0
    failed = []  # List of dicts: {'url': str, 'reason': str}
    
    for article in articles:
        url = article['url']
        schema = article.get('schema')
        
        if not schema:
            error_reason = "no valid schema"
            print(f"  {url}... SKIPPED ({error_reason})")
            failed.append({'url': url, 'reason': error_reason})
            continue
        
        print(f"  {url}...", end=" ", flush=True)
        
        try:
            # Extract site name
            try:
                site = extract_site_from_url(url, site_override)
            except ValueError as e:
                error_reason = f"invalid URL: {str(e)}"
                print(f"FAILED ({error_reason})")
                failed.append({'url': url, 'reason': error_reason})
                continue
            
            # Truncate articleBody
            if 'articleBody' in schema:
                schema['articleBody'] = truncate_text(schema['articleBody'], 20000)
            
            # Create embedding text
            headline = schema.get('headline', '')
            body = schema.get('articleBody', '')
            embedding_text = f"{headline}\n\n{body}"
            
            # Get embedding
            embedding = await get_embedding(embedding_text)
            
            if not embedding:
                error_reason = "no embedding returned"
                print(f"FAILED ({error_reason})")
                failed.append({'url': url, 'reason': error_reason})
                continue
            
            # Generate UUID from URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()
            point_id = str(uuid.UUID(url_hash))
            
            # Create point
            point = models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    'url': url,
                    'name': headline,
                    'site': site,
                    'schema_json': json.dumps(schema, ensure_ascii=True)
                }
            )
            
            # Upload to Qdrant
            await client.upsert(
                collection_name=collection_name,
                points=[point],
                wait=True
            )
            
            print("OK")
            successful += 1
            
            # Small delay to avoid rate limits
            time.sleep(0.25)
        
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            
            if 'token' in error_msg.lower():
                error_reason = "token limit exceeded"
            elif 'rate' in error_msg.lower() or '429' in error_msg:
                error_reason = "rate limit"
            elif 'embedding' in error_msg.lower():
                error_reason = f"embedding error: {error_type}"
            else:
                error_reason = f"{error_type}: {error_msg[:100]}"
            
            print(f"FAILED ({error_reason})")
            failed.append({'url': url, 'reason': error_reason})
    
    return successful, failed


async def main():
    """Main function to process failed uploads"""
    
    parser = argparse.ArgumentParser(
        description='Process and retry failed article uploads',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s failed_upload_20251203_120000.txt data.tsv
  %(prog)s failed_upload_20251203_120000.txt data.tsv --site udn
        """
    )
    parser.add_argument('failed_log', help='Path to failed upload log file')
    parser.add_argument('tsv_file', help='Path to original TSV file')
    parser.add_argument('--site', help='Optional: Override site name')
    
    args = parser.parse_args()
    
    failed_log = args.failed_log
    tsv_file = args.tsv_file
    site_override = args.site
    
    # Print header
    print("="*80)
    print("Failed Upload Processing")
    print("="*80)
    print(f"Failed log: {failed_log}")
    print(f"TSV file: {tsv_file}")
    if site_override:
        print(f"Site: {site_override} (manual override)")
    print("="*80)
    
    # Check files exist
    if not os.path.exists(failed_log):
        print(f"ERROR: Failed log file not found: {failed_log}")
        sys.exit(1)
    
    if not os.path.exists(tsv_file):
        print(f"ERROR: TSV file not found: {tsv_file}")
        sys.exit(1)
    
    # Read failed URLs and reasons from log
    print("\nReading failed upload log...")
    failed_items = []
    source_tsv = None
    
    with open(failed_log, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Parse metadata comments
            if line.startswith('#'):
                if 'Source TSV:' in line:
                    source_tsv = line.split('Source TSV:', 1)[1].strip()
                continue
            
            # Parse data lines: URL<TAB>REASON or just URL
            parts = line.split('\t', 1)
            url = parts[0]
            reason = parts[1] if len(parts) > 1 else ''
            
            failed_items.append({'url': url, 'reason': reason})
    
    print(f"Found {len(failed_items)} failed URLs")
    if source_tsv:
        print(f"Original source: {source_tsv}")
    
    if not failed_items:
        print("\nNo failed URLs to process!")
        return
    
    # If TSV file not provided and we found it in log, try to use it
    if source_tsv and not os.path.exists(tsv_file):
        # Try to find the source TSV in the same directory as the log
        potential_tsv = os.path.join(os.path.dirname(failed_log), source_tsv)
        if os.path.exists(potential_tsv):
            print(f"Using TSV from log metadata: {potential_tsv}")
            tsv_file = potential_tsv
    
    # Classify failures
    categories = await classify_failures(failed_items, tsv_file)
    
    # Display classification results
    print("\n" + "="*80)
    print("Failure Classification")
    print("="*80)
    
    category_names = {
        'missing_from_tsv': 'Missing from TSV',
        'invalid_json': 'Invalid JSON',
        'missing_headline': 'Missing Headline',
        'missing_body': 'Missing Article Body',
        'too_long': 'Article Too Long',
        'unknown': 'Unknown Error (likely rate limit/network)'
    }
    
    for category, items in sorted(categories.items()):
        display_name = category_names.get(category, category)
        print(f"\n{display_name}: {len(items)} articles")
        
        # Show first 3 examples
        for i, item in enumerate(items[:3]):
            print(f"  - {item['url']}")
            if 'reason' in item:
                print(f"    Reason: {item['reason']}")
        
        if len(items) > 3:
            print(f"  ... and {len(items) - 3} more")
    
    print("\n" + "="*80)
    
    # Interactive retry
    all_newly_failed = []
    total_retried = 0
    total_successful = 0
    
    for category, items in sorted(categories.items()):
        display_name = category_names.get(category, category)
        
        # Skip categories that can't be retried
        if category in ['missing_from_tsv', 'invalid_json', 'missing_headline', 'missing_body']:
            print(f"\n{display_name}: Skipping (cannot retry without fixing data)")
            continue
        
        print(f"\n{display_name}: {len(items)} articles")
        response = input(f"Do you want to retry this category? (y/n): ").strip().lower()
        
        if response == 'y':
            print(f"\nRetrying {len(items)} articles...")
            successful, newly_failed = await retry_upload_batch(items, site_override)
            
            total_retried += len(items)
            total_successful += successful
            all_newly_failed.extend(newly_failed)
            
            print(f"Results: {successful} successful, {len(newly_failed)} failed")
    
    # Summary
    print("\n" + "="*80)
    print("Retry Summary")
    print("="*80)
    print(f"Total articles retried: {total_retried}")
    print(f"Successfully uploaded: {total_successful}")
    print(f"Still failed: {len(all_newly_failed)}")
    
    if total_retried > 0:
        success_rate = (total_successful / total_retried) * 100
        print(f"Success rate: {success_rate:.1f}%")
    
    print("="*80)
    
    # Save newly failed URLs with detailed information
    if all_newly_failed:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        new_failed_file = f'failed_upload_{timestamp}.txt'
        new_failed_path = os.path.join(os.path.dirname(failed_log), new_failed_file)
        
        with open(new_failed_path, 'w', encoding='utf-8') as f:
            # Write header with metadata
            f.write(f"# Failed Upload Log (Retry)\n")
            f.write(f"# Source TSV: {os.path.basename(tsv_file)}\n")
            f.write(f"# Original Log: {os.path.basename(failed_log)}\n")
            f.write(f"# Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total Failed: {len(all_newly_failed)}\n")
            f.write(f"#\n")
            f.write(f"# Format: URL<TAB>REASON\n")
            f.write(f"#\n")
            
            # Write failed articles
            for item in all_newly_failed:
                f.write(f"{item['url']}\t{item['reason']}\n")
        
        print(f"\nNewly failed URLs saved to: {new_failed_path}")
        print(f"  (includes source TSV and failure reasons)")
    else:
        print("\nNo newly failed URLs - all retries successful!")


if __name__ == "__main__":
    asyncio.run(main())
