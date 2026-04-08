#!/usr/bin/env python3
"""Validate RSS feeds for quality and recency."""

import feedparser
import requests
import sys
from datetime import datetime, timezone
from email.utils import parsedate_tz
import time
from pathlib import Path

def validate_feed(url, check_age=True):
    """Validate a single RSS feed."""
    print(f'🔍 Validating: {url}')
    
    try:
        # Check if URL is accessible
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ASW-Validator/1.0)'
        })
        response.raise_for_status()
        print(f'  ✅ HTTP Status: {response.status_code}')
    except Exception as e:
        print(f'  ❌ HTTP Error: {e}')
        return False
    
    try:
        # Parse RSS feed
        feed = feedparser.parse(url)
        
        if feed.bozo:
            print(f'  ⚠️  Feed has parsing issues: {feed.bozo_exception}')
        
        if not feed.entries:
            print(f'  ❌ No entries found in feed')
            return False
        
        print(f'  ✅ Found {len(feed.entries)} entries')
        
        if check_age:
            # Check if latest post is within 2 years
            current_time = datetime.now(timezone.utc)
            two_years_ago = current_time.replace(year=current_time.year - 2)
            
            latest_entry = feed.entries[0]
            entry_date = None
            
            # Try to parse entry date
            if hasattr(latest_entry, 'published'):
                try:
                    parsed = parsedate_tz(latest_entry.published)
                    if parsed:
                        entry_date = datetime.fromtimestamp(time.mktime(parsed[:9]), timezone.utc)
                except:
                    pass
            
            if not entry_date and hasattr(latest_entry, 'updated'):
                try:
                    parsed = parsedate_tz(latest_entry.updated)
                    if parsed:
                        entry_date = datetime.fromtimestamp(time.mktime(parsed[:9]), timezone.utc)
                except:
                    pass
            
            if entry_date:
                if entry_date < two_years_ago:
                    print(f'  ❌ Latest post is too old: {entry_date.strftime("%Y-%m-%d")} (older than 2 years)')
                    return False
                else:
                    print(f'  ✅ Latest post date: {entry_date.strftime("%Y-%m-%d")} (within 2 years)')
            else:
                print(f'  ⚠️  Could not parse entry date, but feed is accessible')
        
        # Check feed title
        if hasattr(feed.feed, 'title'):
            print(f'  📝 Feed title: {feed.feed.title}')
        
        return True
        
    except Exception as e:
        print(f'  ❌ Feed parsing error: {e}')
        return False

def main():
    """Main validation function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Validate RSS feeds')
    parser.add_argument('--url', help='Validate a single URL')
    parser.add_argument('--file', default='sources.txt', help='File containing URLs to validate')
    parser.add_argument('--no-age-check', action='store_true', help='Skip age validation')
    parser.add_argument('--max-feeds', type=int, help='Maximum number of feeds to validate')
    
    args = parser.parse_args()
    
    if args.url:
        # Validate single URL
        print(f'📋 Validating single feed...')
        print('=' * 60)
        success = validate_feed(args.url, not args.no_age_check)
        print('=' * 60)
        if success:
            print('✅ Feed validation passed!')
            sys.exit(0)
        else:
            print('❌ Feed validation failed!')
            sys.exit(1)
    
    # Validate from file
    sources_file = Path(args.file)
    if not sources_file.exists():
        print(f'❌ File not found: {args.file}')
        sys.exit(1)
    
    with open(sources_file, 'r') as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    if args.max_feeds:
        urls = urls[:args.max_feeds]
    
    print(f'📋 Validating {len(urls)} feed(s) from {args.file}...')
    print('=' * 60)
    
    failed_feeds = []
    
    for i, url in enumerate(urls, 1):
        print(f'[{i}/{len(urls)}]')
        if not validate_feed(url, not args.no_age_check):
            failed_feeds.append(url)
        print()
    
    print('=' * 60)
    
    if failed_feeds:
        print(f'❌ {len(failed_feeds)} feed(s) failed validation:')
        for url in failed_feeds:
            print(f'  - {url}')
        sys.exit(1)
    else:
        print(f'✅ All {len(urls)} feed(s) passed validation!')

if __name__ == '__main__':
    main()
