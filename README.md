# Arabic Small Web

A curated collection of Arabic-language personal blogs and websites. Inspired by [Kagi Small Web](https://kagi.com/smallweb).

## Local development

```bash
make venv      # Create venv and install deps
make fetch     # Fetch RSS feeds
make build     # Generate static site
make preview   # Open in browser
make deploy    # Full pipeline: clean → fetch → build → preview
make clean     # Remove generated output
```

## Add your site

Open an issue at [MohamedElashri/asw](https://github.com/MohamedElashri/asw/issues) with:
- Site name
- Site URL
- RSS feed URL

## File structure

```
├── sources.json          # Curated RSS feeds
├── translations.json     # EN/AR UI strings
├── scripts/              # Python scripts
│   ├── fetch_feeds.py    # Feed aggregator
│   └── generate_site.py  # Static site generator
├── templates/            # Jinja2 templates
├── static/               # CSS
├── public/               # Generated site (deployed)
└── .github/workflows/    # Daily automation
```

## License

MIT
