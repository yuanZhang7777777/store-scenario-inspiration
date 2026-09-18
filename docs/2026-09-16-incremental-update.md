# Daily catalog update (approved scope)

Goal: reuse previous cleaning and mappings. Product imports are partial upserts,
not replacement exports. Inventory is an independent replacement snapshot.

## Implementation

1. Seed `var/catalog-daily/catalog.sqlite3` once using SQLite backup of the
   immutable full catalog. Preserve the original version and vector caches.
2. Import Excel rows by child SKU; reject conflicting main-SKU reassignment.
   Retain absent children/families. Preserve confirmed exclusions. Store raw
   daily fields (including ERP audit time) separately; do not modify source files.
3. Existing main-SKU retrieval names remain authoritative until a reviewed
   main-SKU name correction is applied. Compare only relevant name/category
   evidence; queue new/changed evidence once per main SKU. Metadata-only updates
   do not require rewriting names or embedding. New main SKUs use existing
   deterministic cleaning, with evidence available for focused correction.
4. `local_catalog.py` reads this daily database when present. Catalog revision
   mismatch blocks stale search until indexing. Existing embeddings remain
   cached by model + actual English text; English projection and fallback names
   are reused. Inventory-only refresh must not re-clean the ERP catalog.

## Bounded acceptance

One focused regression check: partial import retains old children, replay is
idempotent, metadata-only updates preserve names, exclusions survive, identity
conflicts roll back. Run a real new-file preview before applying. Verify source
and original active library unchanged; report exact changed/retained counts.

## Limits

No new model, dependencies, automatic schedule, web service or DingTalk writes.
No speculative quantity aggregation across salesperson rows. Stock quantities
and 单销 retain their source meaning; unresolved overlap must not be fabricated.
This change does not assert that every previous English translation is correct.
Do not replace the whole catalog with the new 429-main export.

This local importer was run with 2,384 daily rows. It currently materializes the
daily workbook in memory (`ponytail`: use bounded streaming batches before daily
exports approach the 600k-row full baseline). No full-export memory/performance
or multi-user service claim is made.

## Work allocation

- GPT-5.5: incremental import module and focused check; data-only name review.
- Main agent: CLI integration, real run, concise operational documentation.
- No commits or unrelated cleanup in this existing dirty worktree.

## Verified run: 2026-09-16

- Daily import: 2,384 rows, 7 new children, 1 new main (`5D0101`), 7 affected
  main documents. Retained all old main/child IDs, including 232 omitted children
  of partially exported families. Daily totals: 223,484 main / 612,967 child.
- GPT-5.5 normalized only 7 pending main names, with source fingerprints and
  provenance; original names remain in baseline/children/raw source rows.
  Re-import produced zero new/changed documents and zero pending reviews.
- Qdrant reused `stock_en_20260910_084051_ffde49`: 2 point updates, 1,065 reused,
  0 deleted. New `catalog_revision=2`. These are 1,067 indexed products, not
  a claim that all 223,484 ERP mains have vectors.
- PH smoke queries `solar panel` and `mobile lamp post` returned corrected mains
  `2B0101` and `2B0711` first in both keyword/vector fused country results.
- Original full SQLite and source Excel hashes unchanged. Eight focused checks
  cover partial retention, replay, exclusion/mapping, name repair, stock-only
  reuse and interrupted-index retry. No repeated whole-project audit.

### Inventory activated after user confirmation: 2026-09-16

The initial strict import stopped on `X1LEDYJD`, absent from both the baseline
and new ERP export. Following the user's activation request, the local CLI now
separately retains unmatched positive-stock source rows while activating known
ERP products. The strict reader default and country/identity/schema checks remain.
Unknown products are not fabricated as ERP documents or silently reported as zero
stock. Search receipts expose the mapping-incomplete warning and original rows.

Active inventory: `真仓库存明细数据-普通商品-汇总数据-1789539410910.xlsx`.
Under the existing `海外仓可发 > 0` rule, matched country pools are PH 437, MY 5,
TH 1, VN 5 (447 unique mains). `X1LEDYJD` remains separately reported for PH.
The same Qdrant collection now has 1,379 points: activation upserted 312 and
reused 1,067, deleted none. A stock-only replay reused all 1,379 without embedding.
Previous state files are retained beside the active state as `.previous-state.json`.

For the user's new inventory-center + public-pool quantity definition, also
missing ERP mains are `X1TZQB` and `XDBWB`. The user confirmed that records must
be retained and missing fields left blank; completing ERP records is not a
prerequisite for continued use. Future quantity reports retain product rows and
left-join the latest inventory on exact main SKU + country: missing values are
null/blank, explicit source zero remains zero, and neither another country's
stock nor previous stock is carried forward. Salesperson-row aggregation remains
unresolved. The current CLI emits SKU lists, not this future quantity report;
no new quantity formula or daily sales aggregation was fabricated.

Runtime evidence: `E:/Project/store-assortment-copilot/var/outputs/2026-09-16-incremental-update/`.
Inventory activation and missing-record evidence:
`E:/Project/store-assortment-copilot/var/outputs/2026-09-16-inventory-activation/`.

### Additive naming trial: prepared, paused before API use

The user subsequently clarified that original Chinese/English names must remain
available in the same logical product record; future standard names and aliases
are additional fields, not replacements. ERP is the product authority; inventory
only joins on product + country. Unmatched inventory is a source-sync exception,
not authority to create an ERP product. The existing `apply-names` projection
update is not used for this new trial.

Twenty source-backed products were prepared in
`var/outputs/2026-09-16-deepseek-name-sample/`, with confirmed exclusions applied.
No DeepSeek requests, credentials stored, catalog updates or vector writes occurred.
The user paused the trial to align the complete business workflow before proceeding.

### Full-source naming sample: resumed and returned, not applied

After business alignment and explicit approval, the trial resumed from the full
`产品列表下载20260831093944283.xlsx`, not the disputed 429-main daily export.
Evidence and results live in
`E:/Project/store-assortment-copilot/var/outputs/2026-09-16-deepseek-full-sample/`.
The previous small-export sample remains historical preparation, not API input
for this run.

Twenty main SKUs cover 668 eligible source child rows. Confirmed exclusions were
applied before four batches of five from official `deepseek-flash`. All 20
returned: model self-labels are 14 `ok`, 6 `needs_review`, not human acceptance
or catalog-wide quality rates. Usage: 61,907 prompt + 3,014 completion = 64,921
tokens. No credential persisted. All original sample fields compared unchanged
after additive merging; the comparison page contains all 20 product cards.

Some incorrect ERP English was corrected; imprecise names and uncertain
category-derived attributes remain. Full rollout is not approved or implied.
Names need focused prompt/input refinement and user review; ordinary variant
differences alone must not block broad main-SKU recall. No `apply-names`, catalog
replacement, new retrieval channel, vector update, inventory formula change or
DingTalk write occurred in this trial.
