# NEXO Database Crawler

A modular, multi-source image dataset crawler. Each website or API has a small adapter, while
HTTP behavior, image downloading, canonical metadata, resume logic, and dataset storage are
shared. The crawler does not generate AI annotations.

The only source currently implemented is the
[Metropolitan Museum of Art Collection API](https://metmuseum.github.io/).

## Architecture

```text
crawler.py                         compatibility CLI entry point
nexo_crawler/
|-- cli.py                         source selection and shared CLI options
|-- http.py                        rate limiting, retries, JSON and binary requests
|-- models.py                      canonical schema 2.0
|-- pipeline.py                    source-independent crawl orchestration
|-- storage.py                     files, JSONL, manifest, resume checks
`-- sources/
    |-- base.py                    SourceAdapter contract
    `-- metmuseum.py               Met discovery, API fetch, and field mapping
```

A future source implements `SourceAdapter` and is registered in
`nexo_crawler/sources/__init__.py`. It does not need to duplicate HTTP, image, JSONL, or resume
code.

## Canonical metadata

Every record in `metadata.jsonl` has the same schema. Missing scalar values are JSON `null`;
known empty lists are `[]`. Empty strings are not used as substitutes for missing data.

```json
{
  "schema_version": "2.0",
  "record_id": "metmuseum:437329:primary",
  "source": {
    "key": "metmuseum",
    "name": "The Metropolitan Museum of Art",
    "object_id": "437329",
    "api_url": "https://collectionapi.metmuseum.org/public/collection/v1/objects/437329",
    "page_url": "https://www.metmuseum.org/art/collection/search/437329",
    "retrieved_at": "2026-08-03T05:00:00Z",
    "raw_path": "raw/metmuseum/MET-437329.json"
  },
  "image": {
    "role": "primary",
    "status": "downloaded",
    "source_url": "https://images.metmuseum.org/...",
    "local_path": "images/metmuseum/MET-437329-primary.jpg",
    "sha256": "...",
    "bytes": 3618033,
    "content_type": "image/jpeg"
  },
  "title": "The Abduction of the Sabine Women",
  "description": null,
  "object_type": "Painting",
  "category": "Paintings",
  "creators": [{"name": "Nicolas Poussin", "role": "Artist"}],
  "creation_date": {
    "display": "probably 1633-34",
    "start_year": 1633,
    "end_year": 1634
  },
  "material": "Oil on canvas",
  "dimensions": "...",
  "culture": null,
  "period": null,
  "country": null,
  "brand": null,
  "model": null,
  "catalog_number": "46.160",
  "department": "European Paintings",
  "tags": null,
  "rights": {
    "public_domain": true,
    "rights_text": null,
    "credit_line": "..."
  },
  "annotation": {
    "status": "source_only",
    "method": "none",
    "reviewed": false,
    "note": "No AI-generated or human-inferred annotations were added."
  },
  "source_metadata": {},
  "crawler_version": "0.2.0"
}
```

`source_metadata` contains useful site-specific values that do not belong in the common schema.
The complete unmodified source response is also retained in `raw/`, so no source data needs to
be invented to fill a common field.

## Output layout

```text
dataset/
|-- images/
|   `-- metmuseum/
|       `-- MET-437329-primary.jpg
|-- raw/
|   `-- metmuseum/
|       `-- MET-437329.json
|-- records/
|   `-- metmuseum/
|       `-- MET-437329-primary.json
|-- metadata.jsonl
`-- crawl_manifest.jsonl
```

Source subdirectories prevent two websites with the same numeric ID from overwriting each
other. One normalized record represents one image sample. The current Met adapter downloads the
primary image only, but the adapter contract supports multiple image candidates per source object.

## Run the Met adapter locally

Python 3.10 or newer is required. No third-party packages or Met API key are required.

```powershell
python crawler.py metmuseum --object-id 437329
```

```powershell
python crawler.py metmuseum --query "sunflowers" --limit 20 --output dataset
```

```powershell
python crawler.py metmuseum --ids-file object_ids.txt --limit 100
```

The first-version commands remain compatible; omitting the source defaults to `metmuseum`:

```powershell
python crawler.py --object-id 437329
```

Metadata only:

```powershell
python crawler.py metmuseum --query "Anna Atkins" --limit 20 --skip-images
```

Run `python crawler.py --help` for source selection help, or
`python crawler.py metmuseum --help` for Met-specific options.

## Resume and rights behavior

Complete records are skipped on later runs. Raw responses cached before an interrupted image
download are reused, and `metadata.jsonl` is rebuilt from individual record files so it does not
accumulate duplicate rows.

The Met adapter downloads an image only when the API explicitly returns
`isPublicDomain: true`. Other metadata is still stored, with the image status set to
`not_public_domain`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The tests are separated by responsibility: canonical schema, Met adapter, common storage, and
source-independent pipeline.
