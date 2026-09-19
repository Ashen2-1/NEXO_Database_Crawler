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
    |-- enrichers/
    |   `-- wikidata.py            exact-entity Wikidata description enrichment
    |-- models.py                  canonical schema 2.2
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
  "schema_version": "2.2",
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
  "description_status": "not_requested",
  "description_source": null,
  "description_source_url": null,
  "description_language": null,
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
  "target_person": true,
  "target_architecture": false,
  "target_painting": true,
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
  "crawler_version": "0.5.0"
}
```

`source_metadata` contains useful site-specific values that do not belong in the common schema.
The complete unmodified source response is also retained in `raw/`, so no source data needs to
be invented to fill a common field.

`metadata.jsonl` is not a reduced summary. Each line is the complete canonical object from one
file under `records/`, serialized without indentation. Training code never needs to join it back
to `records/`. The per-record files exist for atomic updates and resume behavior; JSONL exists for
batch consumption.

Every completed run also rebuilds two flat tables in the same `record_id` order. The training image
column is named `image` and contains the dataset-relative local image path. Missing values are empty
cells.

- `metadata_full.csv` is the complete operational/research table. It includes content, source and
  description provenance, rights, image delivery metadata, external identifiers, crawl fields, and
  annotation-review fields.
- `metadata_ai.csv` is the content-only table intended for AI/model input. It keeps the local image
  path and descriptive artwork/creator fields, but excludes source/original links, all URL columns,
  source provenance, rights and crawl bookkeeping, image hashes/delivery metadata, and
  `annotation_status`, `annotation_method`, `annotation_reviewed`, and `annotation_note`.

`constituent_genders` is not exported in either table. Other common and Met-provided values remain
available in the full table, and the lossless nested source structures remain in JSON.
The obsolete pre-split `metadata.csv` is removed whenever exports are rebuilt, so it cannot be
mistaken for either current table.

Description provenance is explicit in `description_status`, `description_source`,
`description_source_url`, and `description_language`. `description_status` is one of:

- `not_requested`: the run did not use `--enrich-descriptions`;
- `no_source`: the Met object has no object-level Wikidata entity link;
- `not_available`: the linked Wikidata entity has no English description;
- `available`: `description` contains the English description from that exact entity.

The crawler never searches Wikidata by title and never substitutes the artist's biography for the
artwork description. This avoids attaching a plausible but incorrect entity to a training row.
Wikidata descriptions are short source metadata, not detailed visual captions; their quality and
specificity still need to be evaluated for the intended training task.

Neither CSV contains nested JSON cells. Creator data is exposed through scalar columns such
as `creator_name` and `creator_nationality`. Multi-value tags, additional-image URLs, and constituent
fields use ` | ` as a separator and include corresponding count columns. The nested `creators`,
`measurements`, `tag_details`, `constituents`, and complete `source_metadata` structures remain in
`metadata.jsonl` and `records/`, where they can be represented without loss.

Met `dimensions` and `measurements` are related but not identical. `dimensions` is the museum's
human-readable display text. `measurements` may contain several structured groups such as Overall,
Framed, and Weight, so it cannot be mapped safely to one fixed set of scalar CSV columns. The CSV
keeps `dimensions`; JSONL retains both.

CSV quoting is handled automatically, including commas, quotation marks, Unicode text, and
newlines inside descriptions. Training code should normally keep rows where
`image_status == "downloaded"` in the full table; rows without downloadable images remain in all
metadata exports
so the source crawl stays complete and auditable. Alternatively, use the strict `--require-image`
and `--require-description` export requirements documented below.

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
|-- enrichment/
|   `-- metmuseum/
|       `-- MET-437329-description.json
|-- records/
|   `-- metmuseum/
|       `-- MET-437329-primary.json
|-- state/
|   `-- metmuseum/
|       `-- refresh-a1b2c3d4e5f6.json
|-- metadata.jsonl
|-- metadata_full.csv
|-- metadata_ai.csv
`-- crawl_manifest.jsonl
```

Source subdirectories prevent two websites with the same numeric ID from overwriting each
other. One normalized record represents one image sample. The current Met adapter downloads the
primary image only, but the adapter contract supports multiple image candidates per source object.

Each file in `discovery/` records how IDs were selected, the API URL and filters, the reported
total, the discovery time, and the complete discovered ID list. This makes a bulk run
reproducible even if the upstream collection later changes.

`enrichment/` caches the selected object-level Wikidata label, description, language, entity ID,
source URL, and retrieval status. It is separate from the unmodified Met response under `raw/`.

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

### Curated thematic targets

Use `--target` to discover person, architecture, or painting candidates. Targets can be repeated:

```powershell
python crawler.py metmuseum --target person --target painting --limit 150
```

For a training-ready public-domain image batch with source-grounded short descriptions:

```powershell
python crawler.py metmuseum --target person --limit 150 `
  --public-domain-only --enrich-descriptions --output dataset_targeted
```

Target discovery is two-stage. The Met search API supplies candidate IDs; then the crawler checks
each object's own tags, classification, object type, and title before accepting it. Search-only
matches that do not satisfy those source fields are filtered out and do not consume `--limit`.
The `target_person`, `target_architecture`, and `target_painting` CSV columns preserve the final
source-derived flags. A record can match more than one target, such as a painted portrait.

#### Exact target discovery queries

The first stage currently runs these Met Collection API searches in the listed order. Every query
requires `hasImages=true`. Results are combined and duplicate object IDs are removed while
preserving the first-seen order, so highlight results are examined first.

| Target | Candidate searches |
|---|---|
| `person` | `q=Portraits&isHighlight=true`; `q=Men&isHighlight=true`; `q=Women&isHighlight=true`; then the broader `q=Portraits` |
| `architecture` | `q=Architecture` |
| `painting` | `q=painting&medium=Paintings&isHighlight=true`; then `q=painting&medium=Paintings` |

These searches only create a candidate list. A search hit is not automatically labeled as a
target match.

#### Exact target classification rules

For the second stage, the crawler reads the candidate object's own Met API response. Text matching
is case-insensitive. Tag matches below are exact tag-term matches after case normalization; text
field checks use substring matching.

- `target_person=true` when at least one Met tag is `Boys`, `Children`, `Girls`,
  `Human Figures`, `Men`, `People`, `Portraits`, or `Women`; **or** when `classification`,
  `objectName`, or `title` contains `portrait`.
- `target_architecture=true` when at least one Met tag is `Architecture` or `Buildings`; **or**
  when `classification` or `objectName` contains `architect`. This also matches source values such
  as `Architectural` and `Sculpture-Architectural`.
- `target_painting=true` when `classification` or `objectName` contains `painting`. Painting does
  not currently use title or tag matching in the second stage.

In equivalent pseudocode:

```text
person = person_tag_matches
         OR "portrait" in classification
         OR "portrait" in objectName
         OR "portrait" in title

architecture = architecture_tag_matches
               OR "architect" in classification
               OR "architect" in objectName

painting = "painting" in classification
           OR "painting" in objectName
```

When several `--target` options are supplied, a candidate is accepted if **any** requested target
flag is true. All three flags are still calculated and exported, so one record may be both person
and painting. When no `--target` option is used, these flags are still calculated for CSV metadata,
but they do not filter the crawl.

These flags describe the Met's source metadata, not an AI inspection of image pixels. For example,
an object tagged `Men` may be a vessel or piece of furniture that depicts a man as a secondary
decoration; `target_person=true` does not guarantee that a person is the dominant visual subject.
Likewise, target matching does not depend on whether a Wikidata description exists. Stricter
training subsets can additionally filter `classification`, `object_type`, `tags`, title, and
`description_status`, or add human/vision-model review as a separate downstream stage.

Curated targets cannot be combined with IDs, `--query`, `--all`, departments, incremental dates,
or `--include-results-without-images`.

### Broad collection discovery

`--all` requests the Met `objects` endpoint and discovers the collection's available object IDs.
It does not mean that every discovered object is processed in one invocation: the default
`--limit 100` success target still applies.

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

`--limit` is the target number of source objects that must complete successfully in the current
run. Already-complete, filtered, and failed candidates do not consume this success quota. The
crawler continues through the discovered candidate list until the target is reached or the list is
exhausted. With the current Met adapter, one successful object produces one primary-image row.

```powershell
python crawler.py metmuseum --all --limit 500
```

For restrictive filters, use the optional `--max-examined` safety cap to bound the number of
candidates inspected while still treating `--limit` as the success target:

```powershell
python crawler.py metmuseum --target person --year-from 1800 --year-to 2000 `
  --require-creator --limit 100 --max-examined 5000
```

The completion message reports whether the success target was reached, the candidate list was
exhausted, or `--max-examined` stopped the run. A finite source may contain fewer qualifying
objects than requested, so `--limit` cannot create records that do not exist upstream.

Run the same command again to continue. For example:

```text
run 1: save 500 qualifying objects, while skipping/filtering any others encountered
run 2: skip those saved records and save up to 500 additional qualifying objects
run 3: continue with the next incomplete candidates
```

The crawler prints `Success target reached` when the requested number completes successfully.

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

### Public-domain-only training rows

```powershell
python crawler.py metmuseum --target person --public-domain-only --limit 150
```

`rights_public_domain` is copied from the Met API's `isPublicDomain` field. `true` means the Met
marks the work/image as public domain under its Open Access policy. `false` means it is not marked
public domain; it does not necessarily explain the legal reason. The crawler does not download
that image. With `--public-domain-only`, the entire candidate is excluded from records and CSV.

### Source-grounded descriptions

```powershell
python crawler.py metmuseum --target person --enrich-descriptions --limit 150
```

When the Met supplies `objectWikidata_URL`, this option reads the English description from that
exact Wikidata entity and stores its provenance. It does not call a generative AI model and does
not fabricate missing descriptions. Use `description_status == "available"` to select populated
rows; keep the status columns so training code can distinguish missing source data from a run that
did not request enrichment.

### Year, creator, image, and description filters

Use `--year-from` and `--year-to` together or independently to select a creation-year range. A
record is kept when its known `creation_start_year` / `creation_end_year` interval overlaps the
requested interval. This overlap rule preserves approximate dates: for example, a work dated
1790-1810 is included by `--year-from 1800`. Records with no numeric creation year are excluded
whenever either year filter is active.

Use `--require-creator` to keep only rows that contain at least one non-empty creator name. When a
record has multiple creators, the CSV convenience fields come from the first creator with a name.

For example, to keep named creators whose work overlaps 1800-2000:

```powershell
python crawler.py metmuseum --target person --year-from 1800 --year-to 2000 `
  --require-creator --limit 150 --output dataset_targeted
```

Use `--require-image` when every exported training row must have an image that was successfully
downloaded and still exists locally:

```powershell
python crawler.py metmuseum --target person --require-image --limit 150
```

This is stricter than Met search's `hasImages=true`. A candidate is accepted only when its final
canonical image has all of the following:

- `image_status == "downloaded"`;
- a non-empty dataset-relative `image` / `image.local_path`;
- an actual file at that local path when the export is rebuilt.

Candidates with `no_image`, `not_public_domain`, `not_downloadable`, or `skipped` image status are
filtered and do not consume `--limit`. `--require-image` cannot be combined with `--skip-images`.
For the Met adapter, non-public-domain images are never downloaded, so they also cannot satisfy
`--require-image`; `--public-domain-only` is still recommended to state the rights policy
explicitly and filter such objects earlier.

Use `--require-description` when every exported row must contain a non-empty, source-grounded
description:

```powershell
python crawler.py metmuseum --target person --require-description --limit 150
```

`--require-description` automatically enables the same exact-entity Wikidata lookup as
`--enrich-descriptions`. A candidate is accepted only when:

- `description_status == "available"`; and
- `description` is a non-empty string after whitespace is removed.

Therefore `no_source`, `not_available`, `not_requested`, and empty descriptions are filtered and do
not consume `--limit`. This option requires source availability; it does not generate missing text
with AI.

Use both strict requirements for a training export in which every row has both artifacts:

```powershell
python crawler.py metmuseum --target person --limit 150 `
  --public-domain-only --require-image --require-description `
  --year-from 1800 --year-to 2000 --require-creator `
  --output dataset_targeted
```

Strict requirements affect two layers:

1. During crawling, candidates that fail a requirement are recorded in `crawl_manifest.jsonl` as
   `filtered_missing_required_image`, `filtered_missing_required_description`,
   `filtered_missing_creator`, or `filtered_creation_year`; they do not count toward `--limit` and
   do not create a new canonical record.
2. When `metadata.jsonl`, `metadata_full.csv`, and `metadata_ai.csv` are rebuilt, every existing
   record under `records/` is checked again. Active thematic targets such as `--target person` are
   reapplied along with the year, creator, image, and description requirements. Records that do not
   satisfy the current command are excluded even if an older run created them for another target.

Strict export does not delete older `records/`, `raw/`, enrichment caches, or image files. This
preserves crawl provenance and makes the operation reversible. Running a later command without the
strict flags rebuilds the exports without those strict filters. For a permanently strict standalone
dataset, consistently use the same flags and output directory on every continuation run.

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
python crawler.py metmuseum --all --limit 500 --retries 5 --request-delay 1.5
```

HTTP 403 is treated as a potentially temporary source-wide block rather than as a filtered object.
The client retries it with stronger exponential backoff. If access remains blocked after all
retries, the run stops instead of sending hundreds of additional requests; rerun later, preferably
with a larger delay such as `--request-delay 2.0`. HTTP 404 remains an object-level unavailable
candidate and is filtered without consuming the success target.

Provide a custom User-Agent:

```powershell
python crawler.py metmuseum --object-id 437329 --user-agent "NEXO-Crawler/0.2 contact@example.com"
```

Available common options are:

| Option | Default | Meaning |
|---|---:|---|
| `--limit` | `100` | Target number of successfully completed objects in this run |
| `--max-examined` | none | Optional safety cap on all candidate objects examined |
| `--output` | `dataset` | Dataset output directory |
| `--skip-images` | off | Save source metadata without image files |
| `--require-image` | off | Export only rows with a successfully downloaded local image |
| `--public-domain-only` | off | Exclude objects not explicitly marked public domain |
| `--enrich-descriptions` | off | Fetch exact-entity source descriptions when supported |
| `--require-description` | off | Enrich, then export only rows with a non-empty sourced description |
| `--year-from` | none | Keep records whose known creation interval overlaps this year or later |
| `--year-to` | none | Keep records whose known creation interval overlaps this year or earlier |
| `--require-creator` | off | Keep only records with at least one non-empty creator name |
| `--force` | off | Refresh metadata and download images again in a resumable job |
| `--timeout` | `30` | Per-request timeout in seconds |
| `--retries` | `3` | Retries for temporary HTTP failures |
| `--request-delay` | `1.0` | Minimum seconds between requests; raised to at least 1.0 after a recovered 403 |
| `--user-agent` | NEXO default | HTTP User-Agent header |

Run `python crawler.py --help` for source selection help, or
`python crawler.py metmuseum --help` for every Met and common option.

## Resume and rights behavior

Complete records are skipped on later ordinary runs. Raw responses cached before an interrupted
image download are reused, and `metadata.jsonl`, `metadata_full.csv`, plus `metadata_ai.csv` are
rebuilt from individual record files so they do not accumulate duplicate rows.

The run summary separates successful work performed in the current invocation, records that were
already complete, and the final export row count. These numbers are intentionally different when
the output directory already contains reusable records.

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
`not_public_domain`. With `--public-domain-only`, those records are filtered from the dataset
entirely.

## Invalid combinations

The CLI rejects ambiguous or unsupported combinations:

- `--all` with `--query`, `--object-id`, or `--ids-file`;
- `--updated-since` without `--all`;
- `--department-id` without `--query` or `--all`;
- more than one `--department-id` in query mode;
- `--include-results-without-images` without `--query`;
- `--require-image` with `--skip-images`;
- `--year-from` greater than `--year-to`;
- `--target` combined with IDs, `--query`, `--all`, departments, update dates, or image overrides;
- zero or negative limits, timeouts, department IDs, or object IDs.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

After `python -m pip install -e .`, the `PYTHONPATH` line is unnecessary.

The tests are separated by responsibility: canonical schema, Met adapter, common storage, and
source-independent pipeline.
