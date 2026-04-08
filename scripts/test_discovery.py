#!/usr/bin/env python3
"""Test and analyze the discovery algorithm."""

import json
import sys
from collections import Counter
from pathlib import Path

# Add parent directory to path to import generate_site
sys.path.append(str(Path(__file__).parent.parent))

from scripts.generate_site import load_cache, get_all_posts


def analyze_feed_diversity(posts):
    """Analyze the diversity of the generated feed."""
    if not posts:
        print("No posts to analyze.")
        return
    
    # Count posts per site
    site_counts = Counter(post["site_name"] for post in posts)
    
    print(f"\n📊 Feed Analysis ({len(posts)} total posts)")
    print("=" * 50)
    
    print(f"🏠 Sites represented: {len(site_counts)}")
    print(f"📈 Average posts per site: {len(posts) / len(site_counts):.1f}")
    
    print(f"\n📋 Posts per site:")
    for site, count in site_counts.most_common():
        print(f"  • {site}: {count} posts")
    
    # Check for dominance
    max_posts = max(site_counts.values())
    dominant_sites = [site for site, count in site_counts.items() if count == max_posts]
    
    if max_posts > 3:
        print(f"\n⚠️  Potential dominance detected:")
        print(f"   Sites with {max_posts} posts: {', '.join(dominant_sites)}")
    else:
        print(f"\n✅ Good diversity - max posts per site: {max_posts}")
    
    # Time distribution
    recent_posts = sum(1 for post in posts[:len(posts)//2] if post.get("discovery_score", 0) > 0.1)
    print(f"\n⏰ Recent vs Discovery posts:")
    print(f"   Recent-focused: {recent_posts}")
    print(f"   Discovery-focused: {len(posts) - recent_posts}")


def main():
    """Test the discovery algorithm."""
    cache = load_cache()
    if not cache:
        print("❌ No cache file found. Run fetch_feeds.py first.")
        return
    
    print("🔍 Testing Discovery Algorithm")
    print("=" * 50)
    
    # Generate posts with discovery algorithm
    posts = get_all_posts(cache)
    
    # Analyze the results
    analyze_feed_diversity(posts)
    
    print(f"\n🎯 First 10 posts preview:")
    for i, post in enumerate(posts[:10], 1):
        score = post.get("discovery_score", 0)
        print(f"{i:2d}. {post['site_name'][:20]:<20} | {post['title'][:40]:<40} | Score: {score:.3f}")


if __name__ == "__main__":
    main()
