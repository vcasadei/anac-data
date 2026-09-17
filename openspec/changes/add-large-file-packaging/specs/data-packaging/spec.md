# Spec Delta

## Purpose

Turns data files too large for the repository host's per-file limit into size-limited, integrity-checked pieces that can be committed, and rebuilds the originals from those pieces on any machine without the project's own tooling installed.

## ADDED Requirements

### Requirement: Size-threshold packaging

The tool SHALL package a downloaded file into chunks only when the file exceeds a configured size limit, defaulting to 45 MiB. A file at or under the limit MUST be left where it is and MUST NOT be chunked, because it can be committed directly.

#### Scenario: File exceeds the limit

- **WHEN** packaging runs over a dataset whose file is larger than the limit
- **THEN** a chunk set is produced for that file
- **AND** the original file is left in place unchanged

#### Scenario: File is under the limit

- **WHEN** packaging runs over a dataset whose file is smaller than the limit
- **THEN** no chunk set is produced for it
- **AND** the operator is told it needs no packaging

#### Scenario: Limit is overridden

- **WHEN** the operator supplies a different size limit
- **THEN** the decision to chunk, and the size of each chunk, follow the supplied limit

#### Scenario: No file has been downloaded yet

- **WHEN** packaging runs and the dataset's file is not present locally
- **THEN** the tool reports that the file must be downloaded first
- **AND** exits non-zero without writing a partial chunk set

### Requirement: Chunk layout and size

Chunks SHALL be written under `data/dist/<dataset>/`, separate from the raw download tree. Every chunk except the last MUST be exactly the configured size, the last MUST hold the remainder, and each chunk's name MUST carry a zero-padded, lexicographically sortable index so that ordering the names by name restores the original byte order.

#### Scenario: Chunks are ordered by name

- **WHEN** a chunk set is produced
- **THEN** sorting the chunk filenames as text yields the order they must be concatenated in

#### Scenario: Chunk sizes

- **WHEN** a chunk set is produced from a stream larger than the limit
- **THEN** every chunk except the last is exactly the configured size
- **AND** the last chunk is the remainder and is never empty

#### Scenario: Ten or more chunks

- **WHEN** a chunk set contains ten or more chunks
- **THEN** the index padding still sorts them correctly as text

### Requirement: Deterministic compression

The packaged stream SHALL be gzip-compressed, and compression MUST be deterministic: packaging the same bytes twice MUST produce byte-identical chunks. Any timestamp the container format would otherwise embed MUST be pinned to a fixed value.

#### Scenario: Repackaging unchanged data changes nothing

- **WHEN** packaging runs twice over an unchanged file
- **THEN** the chunks produced by the second run are byte-identical to the first
- **AND** their recorded digests are unchanged

#### Scenario: Repackaging changed data produces new chunks

- **WHEN** the source file's content has changed since the last packaging
- **THEN** the chunk set is rewritten to match the new content

### Requirement: Chunk set integrity metadata

Each chunk set SHALL carry, alongside its chunks, the original file's name, byte size and SHA-256 digest; the compressed stream's byte size and SHA-256 digest; the configured chunk size; and every chunk's name, size and SHA-256 digest. This metadata MUST be published in two forms: a structured document for programmatic use, and a plain checksum listing consumable directly by standard command-line checksum tools without a JSON parser.

#### Scenario: Metadata accompanies the chunks

- **WHEN** a chunk set is produced
- **THEN** both the structured metadata and the plain checksum listing are written beside the chunks
- **AND** every recorded field is populated

#### Scenario: Plain listing works with standard tools

- **WHEN** a standard checksum-verification tool is pointed at the plain listing from within the chunk directory
- **THEN** it verifies every chunk without any additional parsing step

### Requirement: Stale chunk removal

Repackaging SHALL leave behind only the chunks belonging to the current content. Chunks from a previous, longer version of the file MUST be removed, so a rebuild can never concatenate a stale chunk onto current data.

#### Scenario: New content needs fewer chunks than before

- **WHEN** a file that previously produced five chunks is repackaged and now needs three
- **THEN** only three chunks remain in the directory
- **AND** the metadata lists exactly those three

### Requirement: Standalone rebuild scripts

The repository SHALL provide a POSIX shell script and a PowerShell script that rebuild an original file from its chunk set. Each MUST run on a fresh clone using only its interpreter and facilities already present on a stock system - no Python, no installation step, no network access, and no dependency on this project's package. Each MUST work when copied out of the repository alongside a chunk directory.

#### Scenario: Rebuild on a machine without Python

- **WHEN** the POSIX script runs on a clone with no Python available
- **THEN** it reconstructs the original file
- **AND** the result is byte-identical to the file that was packaged

#### Scenario: Rebuild on Windows

- **WHEN** the PowerShell script runs on a stock Windows PowerShell installation
- **THEN** it reconstructs the original file
- **AND** the result is byte-identical to the file that was packaged

#### Scenario: Both scripts agree

- **WHEN** the same chunk set is rebuilt by each script
- **THEN** both produce files with the same digest

#### Scenario: Scripts are copied elsewhere

- **WHEN** a script is run from a directory outside the repository, given the location of a chunk set
- **THEN** it rebuilds the file without requiring the rest of the repository

### Requirement: Verified rebuild

A rebuild SHALL verify each chunk's digest before using it and the reconstructed file's digest after decompressing. A failed check MUST be reported with the offending chunk named, MUST exit non-zero, and MUST NOT leave a file that could be mistaken for a correct rebuild.

#### Scenario: A chunk is corrupt

- **WHEN** one chunk's contents do not match its recorded digest
- **THEN** the rebuild names that chunk, fails, and exits non-zero
- **AND** no complete-looking output file is left behind

#### Scenario: A chunk is missing

- **WHEN** a chunk listed in the metadata is not present
- **THEN** the rebuild names the missing chunk and exits non-zero

#### Scenario: The reconstructed file does not match

- **WHEN** every chunk verifies but the decompressed result's digest differs from the recorded original
- **THEN** the rebuild reports the mismatch and exits non-zero

#### Scenario: A successful rebuild says so

- **WHEN** every chunk and the final file verify
- **THEN** the rebuild reports success and exits zero

#### Scenario: Rebuilding over an existing correct file

- **WHEN** the target file already exists and matches the recorded digest
- **THEN** the script reports it is already correct without rewriting it

### Requirement: All data present in the repository

Every data file SHALL be represented in version control. A file within the size limit is tracked as itself; a file over the limit is tracked as its chunk set, and the reassembled original MUST be excluded from version control as a build product. In-progress download artifacts MUST also be excluded.

#### Scenario: Under-limit downloads are tracked

- **WHEN** the required datasets are downloaded into a clean clone
- **THEN** their files are seen by version control as additions rather than ignored

#### Scenario: An oversized original is not tracked

- **WHEN** an oversized file has been downloaded and packaged
- **THEN** version control ignores the oversized original
- **AND** reports its chunks and metadata as tracked content

#### Scenario: Partial downloads are never tracked

- **WHEN** a download is in progress or was interrupted
- **THEN** version control ignores the partial artifact

### Requirement: Reproducible from a fresh clone

The repository SHALL document, and support, reconstructing the complete dataset from a fresh clone. Following the documented steps MUST yield every data file, including those stored as chunks, without requiring the project's Python tooling for the chunked ones.

#### Scenario: A newcomer rebuilds the dataset

- **WHEN** someone clones the repository and follows the documented rebuild steps for their platform
- **THEN** every data file is present locally
- **AND** each rebuilt file matches the digest recorded when it was packaged

#### Scenario: Documentation states what is downloaded

- **WHEN** a newcomer reads the project documentation
- **THEN** it states which datasets the project downloads, which months of data are covered, which are required versus optional, and each file's delimiter, character encoding and header line

### Requirement: Packaging summary reporting

Packaging SHALL report, for each dataset it considered, whether a chunk set was written, skipped as unnecessary, or unchanged since the last run, and end with the total number of chunks and their combined size. The exit status MUST be non-zero if a requested dataset could not be packaged.

#### Scenario: Summary after a mixed run

- **WHEN** packaging runs over one oversized and one under-limit dataset
- **THEN** the summary lists the first as packaged and the second as not needing it
- **AND** exits zero

#### Scenario: Nothing changed since last run

- **WHEN** packaging runs again with no source file changed
- **THEN** each dataset is reported as unchanged
- **AND** the chunk files are not rewritten

#### Scenario: A requested dataset cannot be packaged

- **WHEN** packaging is asked for a dataset whose file is absent
- **THEN** the failure is listed in the summary and the process exits non-zero
