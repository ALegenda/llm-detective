# Production disk measurement — 2026-09-26

Measured twice, at 09:22:02 and 09:22:06 GMT+8, on Render instance `hxbsh`.
Source: [diagnostic deploy b3845d9](https://dashboard.render.com/web/srv-dafqtqlbedkc73flslg0/deploys/dep-darhs7bl550s738f4p90).
The read-only diagnostic runs before SQLite initialization; no story content or credentials are logged.

| Category | Files | Logical bytes | Allocated bytes |
|---|---:|---:|---:|
| `/data/assets` | 549 | 994,272,128 | 995,381,248 |
| `detective.sqlite3` | 1 | 8,470,528 | 8,474,624 |
| `detective.sqlite3-wal` | 1 | 0 | 0 |
| `detective.sqlite3-shm` | 1 | 3 | 0 |

Filesystem: total 1,020,702,720 bytes; used 1,003,925,504 bytes; available to application **0 bytes**. The difference between total minus used and available is reserved filesystem space, not application-writable capacity. Inodes: 65,536 total, 64,971 available; inode exhaustion is excluded.

Images account for **99.1553% of logical application file bytes**, averaging 1.811 MB/file. The database is 8.471 MB. WAL growth is excluded as the current space consumer. These are actual server measurements, not estimates inferred from source code.

Both readonly database access and normal startup fail with `sqlite3.OperationalError: disk I/O error`. Consequently, published/checkpoint/unreferenced image counts and table-level size breakdown remain unavailable. Do not label all 549 files as garbage or claim a measured orphan total.

The source code independently confirms that rejected QA candidates were retained after checkpoint references were overwritten. Local changes now remove a rejected candidate after saving its feedback and protect published images and resumable checkpoints during age-gated orphan collection. Image writes reserve 64 MiB for progress, including a second check after the provider returns. These fixes are pushed, but **not live**: startup still fails before cleanup. No image, database file, or journal was manually deleted; the disk has not been expanded.

Validation: 156 Python tests; 2 Node tests; JavaScript syntax check; successful GitHub CI run 36208151898. Diagnostic deploy failed as expected at SQLite initialization after emitting measurements.

## Recommended recovery and storage design

1. Recover working disk space with a controlled procedure: retain a durable backup before removing any files; identify unreferenced renders using the database reference metadata. If access cannot be restored safely, expanding the disk is a separate paid fallback requiring the user's decision.
2. Activate the tested rejection cleanup and space reserve. Verify database integrity and saved attempts after recovery.
3. Move illustrations into private S3-compatible object storage; retain SQLite and game progress on the persistent filesystem. Preserve current authorization when serving artwork. Copy and verify image objects before changing references or removing local copies.
4. Consider WebP encoding for future images, with visual checks for evidence readability; no compression ratio has been measured yet.

## Current provider comparison

- [Cloudflare R2 Standard](https://developers.cloudflare.com/r2/pricing/): 10 GB-month, 1 million Class A and 10 million Class B operations free per month; then $0.015/GB-month plus excess operation charges. Egress free. Recommended for this app's illustrations; current measured image volume fits within the storage allowance, provided other account usage and operation allowances also fit.
- [Backblaze B2](https://www.backblaze.com/cloud-storage/pricing): first 10 GB free; then $6.95/TB/30 days. Free egress up to 3 times average stored volume, then $0.01/GB unless using a qualifying partner. A/B/C API calls free; D has separate pricing.
- [MinIO AIStor](https://www.min.io/pricing): single-node Free edition exists, but compute and disks must be hosted separately. The official page states original OSS MinIO is no longer maintained. Managed R2 avoids operating another storage server.

No provider account was created, no credentials were added, and no paid storage upgrade was applied.
