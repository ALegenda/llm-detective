# Private illustrations in Cloudflare R2

Production bucket: `llm-detective-assets`, Standard class, public access disabled.
The application uses an account-owned Object Read & Write token restricted to this bucket.
Credentials are stored only in the Render service's environment variables.

## Runtime

Set `ASSET_STORAGE=r2`, `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, and
`R2_SECRET_ACCESS_KEY`. See `.env.example` for variable names; never commit values.

Image generation, QA, portrait reference loading, and authenticated image responses
use memory and R2 directly. They do not write image files or image caches to the
Render filesystem. Existing `assets/<uuid>.png` keys are retained; new renders use
`assets/<uuid>.webp`. Database references and browser URLs stay valid. `/api/assets/<id>` checks both
ownership and player discovery before reading R2. Buckets and hidden clues remain
private. Browser image responses permit private caching for one hour.

New renders default to `gpt-image-2.5-flare`, explicit `low` quality, WebP with
`output_compression=80`, 832×832 portraits/objects and 1152×768 landscapes. The
square and 3:2 framing is preserved. This reduces pixel area by 33.98% and 43.75%
respectively compared with the prior 1024×1024 and 1536×1024 settings. Pixel-area
reduction is not a measured token-saving percentage. WebP compression saves file
bytes, not generation tokens. Log records `IMAGE_CONFIG` and `IMAGE_RENDERED`
report effective settings, actual dimensions/bytes and provider token usage,
without image contents, prompts, credentials or story answers.

The API checks the decoded output format; R2 metadata, authenticated responses,
visual-review data URLs and edit references all use the correct PNG/WebP MIME.
Old PNG checkpoints resume without paying to redraw them. Rejected WebP candidates
are removed through the same verified storage path as PNG candidates. Environment
settings are listed in `.env.example`; changing a code default alone does not
override a pre-existing `OPENAI_IMAGE_MODEL` on Render.

Rejected new candidates are deleted from R2 after the job's feedback checkpoint
is saved. The dedicated bucket's application storage cap defaults to 9,000,000,000
bytes (`R2_MAX_BYTES`), including paginated object listings. The cap is checked
under a process lock before a new write. It is not an account-wide billing cap;
external writers, other buckets, and operation charges are outside its scope.

## Migration and recovery

Set `R2_MIGRATE_LOCAL=1` for the explicit one-time migration. It runs before SQLite
startup and before background workers, so it works even when a full image disk
prevents the database from opening. Render stops the prior disk-backed instance
before launching the new instance.

For each recognized UUID image file, migration uploads without replacing an
existing object, reads the entire object back, compares size and SHA-256, verifies
that the local file has not changed, and only then removes the local copy.
Unknown files or symlinks stop migration. A failed upload or mismatching readback
retains the local file. A restart resumes from remaining files; an already copied
matching object is verified without uploading it again. Progress is emitted as
`R2_MIGRATION` metadata, never image contents or credentials.

After migration, keep `ASSET_STORAGE=r2`. Set `R2_MIGRATE_LOCAL=0` to close the
migration window; an unexpected local image then fails startup instead of being
silently deleted. Do not roll back to a release predating R2 support: the earlier
code expects local files that were intentionally removed. Recovery must use the
same R2 keys or explicitly copy verified objects back before switching to local.

SQLite and saved attempts remain on `/data`. Do not delete the persistent disk.
Removing image files frees capacity but does not reduce the price of an already
provisioned Render disk, and Render does not support shrinking that disk.

References: [Cloudflare S3 SDK setup](https://developers.cloudflare.com/r2/examples/aws/boto3/),
[R2 Standard free allowances and pricing](https://developers.cloudflare.com/r2/pricing/),
[Render persistent disk constraints](https://render.com/docs/disks).

Validation before rollout: 164 Python tests, 2 Node tests, and GitHub CI run
36209569828. Tests cover failed verification, restart, conflicting remote objects,
unknown local files, private image access, remote portrait references, rejection
cleanup, and the storage cap.

## Production verification — 2026-09-26

Deployment `dep-dari832vcj2c73a1rij0` (commit `4dfa943`) completed migration at
09:53:13 GMT+8. All 549 local images, totaling 994,272,128 bytes, were uploaded,
read back, SHA-256 verified, and removed locally. The final remote inventory was
549 objects with exactly the same byte total; local image count was zero.

At 09:53:14, the database opened successfully and the diagnostic reported no
errors, 995,381,248 application-available bytes, and only the 8,470,528-byte SQLite
database in the file inventory. The service became Live at 09:53:18.

`R2_MIGRATE_LOCAL` was subsequently set to `0`. Deployment
`dep-darid417lnhs73dh66c0` restarted successfully at 09:58:06, still with no local
images and the same free-byte count, and became Live at 09:58:10. The saved
planetarium attempt opened through the normal player interface, and its existing
Marina and Galina portraits were visually confirmed. A workshop-image retry was
accepted by the UI but stopped on the pre-existing AI operation budget; this is
not evidence of a completed new-image generation. New image generation remains
to be verified once that authorized testing budget is lifted.

The provisioned Render disk remains 1 GB for SQLite and saves. No disk expansion
or deletion was performed, and freeing image capacity does not cancel its charge.

### Compact image profile verified in production

Render still had `OPENAI_IMAGE_MODEL=gpt-image-1.5`, overriding the newer code
default. It was changed to `gpt-image-2.5-flare`. The effective startup config
confirmed low quality, WebP compression 80, square 832×832 and landscape 1152×768.
Live generation produced a 60,588-byte base portrait (143 input / 173 output
tokens) and a 61,140-byte expression edit (1,203 input / 173 output tokens).
Both were accepted and displayed in the normal player interface.

The workshop and foyer initially exhausted retries because review confused
ordinary room props and venue signage with blocking defects. Location review
now receives public room context and specific interaction-target exteriors,
separately from optional generation directions. It still rejects recognizable
clues, spoilers and invented evidence. After commit `3b333c0` went Live in deploy
`dep-daritnbl550s738fsu50`, both retried jobs completed on their first new
candidate: workshop 139,506 bytes (145 input / 127 output tokens), foyer 110,492
bytes (164 input / 127 output tokens). The workshop was visually checked in game.

The measured output-token counts are 36.4% below the previous GPT Image 1.5 low
1024×1024 count of 272, and 68.25% below its low 1536×1024 count of 400.
These are per-generation comparisons against documented prior-model counts,
not matched-prompt experiments or total-cost savings; input, QA and retries
also cost tokens. Existing PNGs were not recompressed or regenerated.

Validation: 179 Python tests and GitHub CI `36212044688` passed. New R2 renders
left no local image files at subsequent startup. Temporary unlimited testing
was closed by restoring attempt/case budgets to 200/140 and the user-approved
daily budget to 800 (environment deployment `dep-dariuo8jo6nc7385ommg`).

Model and output controls: [GPT Image 2.5 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare),
[image generation guide](https://developers.openai.com/api/docs/guides/image-generation).
