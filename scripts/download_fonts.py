#!/usr/bin/env python3
"""Download fonts locally to avoid CDN dependencies."""

import urllib.request
from pathlib import Path

def download_fonts():
    """Download Noto Sans Arabic fonts locally."""
    fonts_dir = Path(__file__).parent.parent / "static" / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    
    fonts = [
        {
            "url": "https://fonts.gstatic.com/s/notosansarabic/v33/nwpxtLGrOAZMl5nJ_wfgRg3DrWFZWsnVBJ_sS6tlqHHFlhQ5l3sQWIHPqzCfyGyvuw.ttf",
            "filename": "noto-sans-arabic-400.ttf"
        },
        {
            "url": "https://fonts.gstatic.com/s/notosansarabic/v33/nwpxtLGrOAZMl5nJ_wfgRg3DrWFZWsnVBJ_sS6tlqHHFlhQ5l3sQWIHPqzCfL2uvuw.ttf", 
            "filename": "noto-sans-arabic-700.ttf"
        }
    ]
    
    for font in fonts:
        font_path = fonts_dir / font["filename"]
        if not font_path.exists():
            print(f"Downloading {font['filename']}...")
            try:
                urllib.request.urlretrieve(font["url"], font_path)
                print(f" Downloaded {font['filename']}")
            except Exception as e:
                print(f" Failed to download {font['filename']}: {e}")
        else:
            print(f" {font['filename']} already exists")

if __name__ == "__main__":
    download_fonts()
