# NEXO Database Crawler — Met Museum v1

This first version collects **source-grounded metadata** and public-domain images from the
[Metropolitan Museum of Art Collection API](https://metmuseum.github.io/). It does not call
an AI model and does not invent captions, dates, categories, physical properties, facial
biometrics, or other annotations that are absent from the Met record.

The crawler uses only Python's standard library. No package installation or API key is needed.

## What it outputs

```text
dataset/
├── images/                  # downloaded public-domain primary images
│   └── MET-437329.jpg
├── raw/                     # complete Met API responses for provenance
│   └── MET-437329.json
├── records/                 # one normalized, source-only record per object
│   └── MET-437329.json
├── metadata.jsonl           # all normalized records, one JSON object per line
└── crawl_manifest.jsonl     # completed, skipped, and failed crawl events
```

`metadata.jsonl` is the main dataset index. Its `image` field is a relative local path when a
public-domain image was downloaded. Original Met values are kept as clearly named raw fields,
such as `object_date_raw`, `medium_raw`, and `dimensions_raw`. Every row also links to its raw
API response and source page.

The file is suitable as a clean ingestion layer for later review or annotation. It is **not yet
a caption-training dataset**, because the script intentionally does not turn titles and catalog
fields into synthetic image descriptions.

## Run it locally

Use Python 3.10 or newer. From this directory:

```powershell
python crawler.py --object-id 437329
```

Several known object IDs:

```powershell
python crawler.py --object-id 437329 --object-id 436535 --output dataset
```

A text file containing one object ID per line:

```powershell
python crawler.py --ids-file object_ids.txt --limit 100 --output dataset
```

A Met API search (image results only by default):

```powershell
python crawler.py --query "sunflowers" --limit 20 --output dataset
```

Metadata only, without downloading image files:

```powershell
python crawler.py --query "Anna Atkins" --limit 20 --skip-images
```

Run `python crawler.py --help` for all options.

## Safe restart behavior

Running the same command again skips complete records. `metadata.jsonl` is rebuilt from the
individual files in `records/`, so reruns do not append duplicate training rows. Failed objects
are logged and retried on a later run. Use `--force` only when you intentionally want to fetch
completed objects again.

The crawler defaults to at most 100 objects per invocation and waits 0.25 seconds between HTTP
requests. It will never crawl the entire collection merely because it was run without arguments.

## Image rights boundary

The script downloads an image only when the Met API explicitly returns `isPublicDomain: true`.
All other records can still be saved as metadata, with `image_status` set to
`not_public_domain`. Keep the Met source URL, credit line, rights field, and public-domain flag
with any downstream copy of the data.

## Test it

```powershell
python -m unittest discover -s tests -v
```

## Current scope

- One source: the Met Collection API.
- Primary images only; additional image URLs are preserved but not downloaded.
- No browser automation and no HTML scraping.
- No AI annotation or subjective template fields.
- Intended to run on a local machine in small, resumable batches.
