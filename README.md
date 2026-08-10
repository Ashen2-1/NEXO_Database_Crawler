# NEXO Database Crawler

A modular, multi-source image dataset crawler. Each website or API has a small adapter, while
HTTP behavior, image downloading, canonical metadata, resume logic, and dataset storage are
shared. The crawler does not generate AI annotations.

The only source currently implemented is the
[Metropolitan Museum of Art Collection API](https://metmuseum.github.io/).

## Architecture

```text
crawler.py                         compatibility CLI entry point
src/
`-- nexo_crawler/
    |-- cli.py                     source selection and shared CLI options
    |-- http.py                    rate limiting, retries, JSON and binary requests
    |-- models.py                  canonical schema 2.1
    |-- pipeline.py                source-independent crawl orchestration
    |-- storage.py                 files, JSONL/CSV exports, manifest, resume checks
    `-- sources/
        |-- base.py                SourceAdapter contract
        `-- metmuseum.py           Met discovery, API fetch, and field mapping
```

A future source implements `SourceAdapter` and is registered in
`src/nexo_crawler/sources/__init__.py`. It does not need to duplicate HTTP, image, JSONL, or
resume code.

## Canonical metadata

Every record in `metadata.jsonl` has the same schema. Missing scalar values are JSON `null`;
known empty lists are `[]`. Empty strings are not used as substitutes for missing data.

```json
{
  "schema_version": "2.1",
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
  "classification": "Paintings",
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
  "dynasty": null,
  "reign": null,
  "portfolio": null,
  "country": null,
  "region": null,
  "subregion": null,
  "locale": null,
  "city": null,
  "state": null,
  "county": null,
  "geography_type": null,
  "locus": null,
  "excavation": null,
  "river": null,
  "brand": null,
  "model": null,
  "catalog_number": "46.160",
  "accession_year": "1946",
  "department": "European Paintings",
  "repository": "Metropolitan Museum of Art, New York, NY",
  "object_wikidata_url": null,
  "gallery_number": "617",
  "is_highlight": false,
  "is_timeline_work": false,
  "link_resource": null,
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
  "crawler_version": "0.3.0"
}
```

`source_metadata` contains useful site-specific values that do not belong in the common schema.
The complete unmodified source response is also retained in `raw/`, so no source data needs to
be invented to fill a common field.

`metadata.jsonl` is not a reduced summary. Each line is the complete canonical object from one
file under `records/`, serialized without indentation. Training code never needs to join it back
to `records/`. The per-record files exist for atomic updates and resume behavior; JSONL exists for
batch consumption.

Every completed run also rebuilds `metadata.csv` for training tools that prefer tabular input.
It contains the same records in the same `record_id` order. Nested scalar fields use names such
as `source_object_id`, `image_status`, and `rights_public_domain`; the training image column is
named `image` and contains the dataset-relative local image path. Missing values are empty cells.
Common and Met-provided fields are expanded into explicit columns, including `classification`,
creator details, creation dates, material, dimensions, measurements, culture, period, dynasty,
country, region, subregion, locale, city, state, county, department, repository, accession data,
rights, source URLs, image provenance, and Wikidata identifiers. All columns are always present;
unavailable values are empty.

The `creators`, `tags`, `tag_details`, `measurements`, `additional_image_urls`, `constituents`, and
`source_metadata` cells contain compact JSON because those values can contain lists or objects and
cannot be represented losslessly as ordinary scalar CSV columns. First-creator convenience columns
such as `creator_name` and `creator_nationality` are also provided for tools that only accept scalar
columns.

CSV quoting is handled automatically, including commas, quotation marks, Unicode text, and
newlines inside descriptions. Training code should normally keep rows where
`image_status == "downloaded"`; rows without downloadable images remain in both metadata exports
so the source crawl stays complete and auditable.

## Output layout

```text
dataset/
|-- images/
|   `-- metmuseum/
|       `-- MET-437329-primary.jpg
|-- discovery/
|   `-- metmuseum/
|       `-- 20260803T120000.000000Z-all.json
|-- raw/
|   `-- metmuseum/
|       `-- MET-437329.json
|-- records/
|   `-- metmuseum/
|       `-- MET-437329-primary.json
|-- state/
|   `-- metmuseum/
|       `-- refresh-a1b2c3d4e5f6.json
|-- metadata.jsonl
|-- metadata.csv
`-- crawl_manifest.jsonl
```

Source subdirectories prevent two websites with the same numeric ID from overwriting each
other. One normalized record represents one image sample. The current Met adapter downloads the
primary image only, but the adapter contract supports multiple image candidates per source object.

Each file in `discovery/` records how IDs were selected, the API URL and filters, the reported
total, the discovery time, and the complete discovered ID list. This makes a bulk run
reproducible even if the upstream collection later changes.

`state/` stores resumable refresh-job state for `--updated-since` and `--force`. It is part of
the dataset's operational state and should be kept with the other output files.

## Setup and entry points

Python 3.10 or newer is required. No third-party packages or Met API key are required.

The compatibility entry point works directly from a fresh checkout and is the shortest option:

```powershell
python crawler.py metmuseum --object-id 437329
```

For normal package development, create a virtual environment and install it in editable mode:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
nexo-crawler metmuseum --object-id 437329
```

After editable installation, module execution also works:

```powershell
python -m nexo_crawler metmuseum --object-id 437329
```

The old first-version form remains compatible; omitting the source defaults to `metmuseum`:

```powershell
python crawler.py --object-id 437329
```

The examples below use the explicit `metmuseum` source form.

## Met discovery modes

At least one discovery mode is required. The crawler never interprets an empty command as
permission to enumerate the whole collection.

### One known object ID

```powershell
python crawler.py metmuseum --object-id 437329
```

### Several known object IDs

```powershell
python crawler.py metmuseum --object-id 437329 --object-id 436535 --object-id 437112
```

### IDs from a text file

`object_ids.txt` contains one numeric ID per line. Blank lines and lines beginning with `#` are
ignored.

```text
# European Paintings
437329
436535
```

```powershell
python crawler.py metmuseum --ids-file object_ids.txt --limit 100
```

`--object-id` and `--ids-file` can be combined. Duplicate IDs are removed while preserving their
first-seen order.

### Keyword search

Search results are limited to objects with images by default:

```powershell
python crawler.py metmuseum --query "sunflowers" --limit 20
```

Search within one Met department:

```powershell
python crawler.py metmuseum --query "vase" --department-id 5 --limit 100
```

Include search results that do not have images:

```powershell
python crawler.py metmuseum --query "sunflowers" --include-results-without-images --limit 100
```

Direct IDs or an IDs file can be combined with a query. The result is deduplicated:

```powershell
python crawler.py metmuseum --object-id 437329 --query "sunflowers" --limit 20
```

Met search accepts only one `--department-id` per query.

### Broad collection discovery

`--all` requests the Met `objects` endpoint and discovers the collection's available object IDs.
It does not mean that every discovered object is processed in one invocation: the default
`--limit 100` safety cap still applies.

```powershell
python crawler.py metmuseum --all
```

Process up to 1,000 incomplete objects in this batch:

```powershell
python crawler.py metmuseum --all --limit 1000
```

Discover one department:

```powershell
python crawler.py metmuseum --all --department-id 11 --limit 500
```

Discover several departments by repeating the filter:

```powershell
python crawler.py metmuseum --all --department-id 5 --department-id 11 --limit 500
```

The Met inventory endpoint does not provide an image-only filter. In `--all` mode the crawler
must inspect each returned object record to learn whether an image is present and downloadable.

### Incremental discovery

Discover objects whose Met metadata changed after a date:

```powershell
python crawler.py metmuseum --all --updated-since 2026-08-01 --limit 500
```

Combine incremental discovery with departments:

```powershell
python crawler.py metmuseum --all --updated-since 2026-08-01 --department-id 11 --limit 500
```

`--updated-since` accepts `YYYY-MM-DD` and requires `--all`.

Unlike an ordinary `--all` run, incremental discovery does not skip an ID merely because it was
ingested before. Every returned ID is fetched once in the current refresh job. The new API JSON
is compared with the cached JSON and reported as:

- `updated`: source metadata changed;
- `unchanged`: the object was checked but its API JSON did not change;
- `created`: the ID did not exist locally.

Run the same incremental command again while the job is in progress to continue with the next
unchecked IDs. The job start time is stored in `state/`; records retrieved after that time count
as checked for this job. When the entire discovered list has been checked, the job is marked
completed. Running the command again after completion starts a new refresh job.

When metadata changes but the primary image URL is unchanged and the local image is complete,
the existing image file and hash are reused. The image is downloaded again only when it is new,
missing, no longer matches the source URL, or `--force` was requested.

## Batch, output, and HTTP options

### Batch size and automatic continuation

`--limit` is the maximum number of source objects requiring work that are attempted in the
current run. In an ordinary crawl, already complete objects are skipped. In a refresh job,
objects already checked after the job started are skipped. Neither kind of skip consumes the
limit. Failed attempts do consume it, preventing an error-heavy run from becoming unbounded.

```powershell
python crawler.py metmuseum --all --limit 500
```

Run the same command again to continue. For example:

```text
run 1: process the first 500 incomplete objects
run 2: skip those 500 and process the next 500
run 3: continue with the next incomplete objects
```

The crawler prints `Batch limit reached` when another incomplete object remains in the discovery
result.

### Choose the dataset directory

```powershell
python crawler.py metmuseum --query "sunflowers" --limit 20 --output "D:\NEXO\met-dataset"
```

The default output directory is `dataset/` under the current working directory.

### Metadata only

Do not download image binaries:

```powershell
python crawler.py metmuseum --query "Anna Atkins" --limit 20 --skip-images
```

The raw API payload and canonical metadata are still saved. A new image candidate receives the
`skipped` status; an already downloaded, unchanged image may retain its existing downloaded
status and local path during a metadata refresh.

### Force a refresh

Normally complete records are reused. `--force` creates a resumable refresh job, fetches and
rewrites existing metadata, and downloads the images again. Forced records consume the batch
limit:

```powershell
python crawler.py metmuseum --object-id 437329 --force
```

For a forced bulk refresh, repeat the same command until the refresh job is completed:

```powershell
python crawler.py metmuseum --all --department-id 11 --force --limit 500
```

### HTTP controls

Change the request timeout:

```powershell
python crawler.py metmuseum --object-id 437329 --timeout 60
```

Change retry count and polite delay between requests:

```powershell
python crawler.py metmuseum --all --limit 500 --retries 5 --request-delay 0.5
```

Provide a custom User-Agent:

```powershell
python crawler.py metmuseum --object-id 437329 --user-agent "NEXO-Crawler/0.2 contact@example.com"
```

Available common options are:

| Option | Default | Meaning |
|---|---:|---|
| `--limit` | `100` | Maximum objects requiring work attempted in this run |
| `--output` | `dataset` | Dataset output directory |
| `--skip-images` | off | Save source metadata without image files |
| `--force` | off | Refresh metadata and download images again in a resumable job |
| `--timeout` | `30` | Per-request timeout in seconds |
| `--retries` | `3` | Retries for temporary HTTP failures |
| `--request-delay` | `0.25` | Minimum seconds between requests |
| `--user-agent` | NEXO default | HTTP User-Agent header |

Run `python crawler.py --help` for source selection help, or
`python crawler.py metmuseum --help` for every Met and common option.

## Resume and rights behavior

Complete records are skipped on later ordinary runs. Raw responses cached before an interrupted
image download are reused, and `metadata.jsonl` plus `metadata.csv` are rebuilt from individual
record files so they do not accumulate duplicate rows.

When the canonical schema version changes, older records are treated as incomplete and normalized
again from their cached `raw/` response. An unchanged downloaded image is reused, so a schema
upgrade does not require downloading the image again.

Every run performs discovery again and writes a timestamped snapshot. Record completion is
determined from `raw/`, `records/`, and the expected local image file rather than from an in-memory
cursor, so rerunning after a crash is safe.

Incremental and forced refreshes additionally use the stable job start time in `state/`. A crash
may leave the job marked in progress, but records already written with a later `retrieved_at`
are recognized on the next run and do not consume the next batch.

The Met adapter downloads an image only when the API explicitly returns
`isPublicDomain: true`. Other metadata is still stored, with the image status set to
`not_public_domain`.

## Invalid combinations

The CLI rejects ambiguous or unsupported combinations:

- `--all` with `--query`, `--object-id`, or `--ids-file`;
- `--updated-since` without `--all`;
- `--department-id` without `--query` or `--all`;
- more than one `--department-id` in query mode;
- `--include-results-without-images` without `--query`;
- zero or negative limits, timeouts, department IDs, or object IDs.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

After `python -m pip install -e .`, the `PYTHONPATH` line is unnecessary.

The tests are separated by responsibility: canonical schema, Met adapter, common storage, and
source-independent pipeline.
