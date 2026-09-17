# Spec Delta

## Purpose

Discovers, downloads, verifies and catalogues ANAC open-data files from the agency's public directory-index portal into a local raw-data tree, so the course's datasets can be reproduced on any machine without manual browser clicking.

## ADDED Requirements

### Requirement: Listing-driven discovery

The tool SHALL determine which files to download by reading the portal's directory listings at run time. It MUST NOT construct a download URL from a hardcoded or pattern-guessed filename, because ANAC has changed its naming conventions repeatedly.

#### Scenario: Filename pattern differs from expectation

- **WHEN** a target folder's listing contains a file whose name does not match any previously observed naming pattern
- **THEN** the tool downloads the file as listed
- **AND** does not fail or fall back to a guessed URL

#### Scenario: Target folder is missing from the portal

- **WHEN** a required folder in the navigation path returns HTTP 404 or is absent from its parent listing
- **THEN** the tool reports which path segment was not found, along with the entries that were present at that level
- **AND** exits non-zero without writing a partial dataset

### Requirement: VRA month selection by folder content

The tool SHALL download VRA monthly files for the N most recent months that actually contain a matching data file, defaulting to N=3. Selection MUST be based on the presence of files inside a month folder, not on the folder's name or its modification date, because the portal pre-creates empty folders for future months of the current year.

#### Scenario: Current year contains empty future-month folders

- **WHEN** the latest year folder lists month folders beyond the last published month and those folders are empty
- **THEN** those months are excluded from selection
- **AND** the most recent months holding data are selected instead

#### Scenario: Latest year holds fewer months than requested

- **WHEN** the most recent year folder contains fewer than N months with data
- **THEN** the tool continues into the preceding year's folder to complete the selection
- **AND** the selected months are contiguous and the most recent available overall

#### Scenario: Requested month count is overridden

- **WHEN** the operator requests a different number of months
- **THEN** that many of the most recent months holding data are selected

### Requirement: Required and optional dataset scope

The tool SHALL download the VRA monthly files and the public-aerodrome list on a default run. It SHALL download the air-transport statistical dataset only when the operator explicitly opts in.

#### Scenario: Default run

- **WHEN** the tool runs with no dataset flags
- **THEN** the selected VRA months and the current public-aerodrome list are downloaded
- **AND** the statistical dataset is not requested over the network

#### Scenario: Operator opts into the optional dataset

- **WHEN** the operator passes the flag enabling optional data
- **THEN** the full statistical CSV is downloaded in addition to the required datasets
- **AND** the operator is informed of its size before the transfer begins

#### Scenario: Operator selects a single dataset

- **WHEN** the operator names one dataset to fetch
- **THEN** only that dataset is discovered and downloaded

### Requirement: Local layout

Downloaded files SHALL be written under `data/raw/anac/<dataset>/`, one subdirectory per dataset (`vra`, `aerodromos`, `estatisticos`), preserving the filename the portal published. The destination root MUST be overridable by the operator.

#### Scenario: Files land in their dataset directory

- **WHEN** a default run completes
- **THEN** each VRA month file is at `data/raw/anac/vra/<published-name>` and the aerodrome list is at `data/raw/anac/aerodromos/<published-name>`

#### Scenario: Destination root is overridden

- **WHEN** the operator supplies an alternate output root
- **THEN** the same `<dataset>/` structure is created beneath that root instead

### Requirement: Idempotent re-runs

A re-run SHALL NOT re-transfer a file whose remote copy is unchanged. The tool MUST decide using the portal-supplied `ETag` and `Last-Modified` values recorded for the local copy.

#### Scenario: Remote file unchanged since last run

- **WHEN** the tool re-runs and the remote validators match those recorded for the existing local file
- **THEN** the file is reported as up to date and its bytes are not transferred again

#### Scenario: Remote file has been republished

- **WHEN** the remote validators differ from those recorded locally
- **THEN** the file is downloaded again and replaces the local copy

#### Scenario: Operator forces refresh

- **WHEN** the operator requests a forced refresh
- **THEN** the file is re-downloaded regardless of the recorded validators

### Requirement: Interrupted transfers resume

The tool SHALL resume an interrupted download rather than restarting it, using HTTP range requests when the server honours them. A partially written file MUST never be presented as a complete download.

#### Scenario: Transfer is interrupted and retried

- **WHEN** a download stops partway and the tool runs again
- **THEN** the transfer continues from the byte offset already stored
- **AND** the resulting file matches the size the portal reported

#### Scenario: Server does not honour a range request

- **WHEN** the server ignores the range request and responds with the full body
- **THEN** the tool restarts that file's download cleanly rather than appending to the partial data

#### Scenario: Partial file is never mistaken for complete

- **WHEN** a download is interrupted
- **THEN** no file at the final destination path contains truncated content
- **AND** a subsequent consumer either sees the complete file or no file

### Requirement: Integrity verification

After each transfer the tool SHALL verify the received byte count against the size the portal advertised, and MUST compute a SHA-256 digest of the stored file. A size mismatch MUST be treated as a failed download.

#### Scenario: Received size does not match advertised size

- **WHEN** a completed transfer's byte count differs from the advertised `Content-Length`
- **THEN** the tool reports the mismatch, discards the file from its final location, and exits non-zero

#### Scenario: Portal advertises no size

- **WHEN** the response carries no `Content-Length`
- **THEN** the tool stores the file, records its digest, and notes that the size could not be verified

### Requirement: Run manifest

The tool SHALL write a machine-readable manifest recording, for every file it manages, the source URL, the local path, the byte size, the SHA-256 digest, the portal's `ETag` and `Last-Modified` values, the dataset's own `Atualizado em:` date as stated in the file, and the time the download completed. The manifest MUST be updated on every run so it always describes what is currently on disk.

#### Scenario: Manifest is written after a successful run

- **WHEN** a run downloads one or more files
- **THEN** the manifest contains one entry per managed file with all recorded fields populated

#### Scenario: Manifest survives a skipped file

- **WHEN** a re-run skips an unchanged file
- **THEN** that file's existing manifest entry is retained rather than dropped

#### Scenario: Manifest reflects a partially failed run

- **WHEN** one dataset fails while another succeeds
- **THEN** the manifest records the files that were successfully stored
- **AND** the failed file is absent rather than recorded with placeholder values

### Requirement: CSV dialect report

For each downloaded CSV the tool SHALL determine and report the field delimiter, the character encoding, whether a byte-order mark is present, and the 1-based line number of the real header row. These values MUST be derived from the file's actual bytes, not assumed from the dataset.

#### Scenario: Encoding differs between datasets

- **WHEN** the dialect report is produced for a default run
- **THEN** VRA is reported as UTF-8 with a BOM and the aerodrome list as Latin-1, each derived from its own content

#### Scenario: File opens with a metadata line before the header

- **WHEN** a file's first line is an `Atualizado em:` metadata line rather than column names
- **THEN** the report identifies the header as line 2
- **AND** exposes the `Atualizado em:` date as the dataset's publication date

#### Scenario: Header is on the first line

- **WHEN** a downloaded file's first line already holds column names
- **THEN** the report identifies the header as line 1 without inventing a metadata line

#### Scenario: Encoding cannot be determined confidently

- **WHEN** a file decodes cleanly under neither candidate encoding
- **THEN** the report states that the encoding is undetermined rather than asserting one

### Requirement: Polite, resilient network use

The tool SHALL issue read-only requests sequentially, pause briefly between them, identify itself with a descriptive user agent, and retry transient failures with increasing delays before giving up. It MUST NOT retry a response that indicates the file does not exist.

#### Scenario: Transient network failure

- **WHEN** a request fails with a timeout or a server-side error
- **THEN** the tool retries it after an increasing delay, up to a bounded number of attempts
- **AND** reports the failure with its cause if all attempts are exhausted

#### Scenario: File is genuinely absent

- **WHEN** a request returns HTTP 404
- **THEN** the tool reports it immediately without retrying

### Requirement: Progress and summary reporting

The tool SHALL report which files it is downloading and end each run with a summary listing files downloaded, files skipped as unchanged, and files that failed. The exit status MUST be non-zero if any required file could not be obtained.

#### Scenario: Run summary after mixed outcomes

- **WHEN** a run downloads some files, skips others and fails on one required file
- **THEN** the summary lists each file under the correct outcome
- **AND** the process exits non-zero

#### Scenario: All required files obtained

- **WHEN** every required file is downloaded or already up to date
- **THEN** the process exits zero

#### Scenario: Operator previews without downloading

- **WHEN** the operator requests a dry run
- **THEN** the tool reports the files it would download, with their URLs and sizes
- **AND** transfers no file content
