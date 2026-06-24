---
name: data_export
id: data_export
tools: [http_request, file]
tags: [data, export, csv, api]
metadata:
  short-description: Fetch paginated records from a REST API and write them to a CSV file
---

# Summary
Pull records from a paginated REST API endpoint and write the results to a local CSV file for
downstream processing.

# Procedure
- Read the API base URL, endpoint path, auth token, and output filename from config.toml.
- Fetch page 1: GET {base_url}/{endpoint}?page=1&per_page=100 with Authorization: Bearer {token}.
- Extract the records array and total_pages (or next_cursor) from the response.
- Repeat for subsequent pages until all records are fetched or page limit (10) is reached.
- Flatten each record to a row: extract the fields listed in config.toml (fields key).
- Write a header row and all data rows to the output CSV file.
- Append a summary line: total records fetched, pages retrieved, timestamp.

# Verification
- Confirm the first API response returned 200 and the records array is non-empty.
- Confirm the output CSV file exists and has more than 1 line (header + at least one data row).
- Confirm the record count in the summary line matches the actual row count in the file.
