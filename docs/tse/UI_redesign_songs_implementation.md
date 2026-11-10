# Segment discovery implementation

Both pages share `src/features/song-discovery` (sections, tables, marks, recommendations,
search/pagination and MIDI previews). Official entries use segment IDs; local entries
retain source/ID identity. Practice keeps Folders and catalog prefetch.

Recommendations read existing `piano_recommendations.response.items`, requiring
`is_demo = false`. Batches are scanned newest first and joined to the current published
catalog; the newest batch containing playable segments wins. No inference is triggered.

Marks use the authenticated user's ID and SWR's per-user shared cache. Writes are
idempotent, duplicate clicks are guarded, and failed writes retain the prior UI state.
Inline previews use the existing Player without a MIDI input subscription; each load
is generation-checked before playback, including after soundfont initialization.

## Deployment

1. Apply `supabase/migrations/020_segment_marks.sql` after migrations 018 and 019,
   using the normal Supabase migration workflow (or execute this SQL in its SQL editor).
   It creates a new table with SELECT/INSERT/DELETE policies scoped to `auth.uid()`;
   existing data and recommendation algorithms are unchanged.
2. Deploy the frontend produced by `npm run build`.
3. With two test accounts, verify an account cannot select/delete the other's marks
   or insert a mark with the other's `user_id`. UPDATE and anonymous access are denied.
4. Verify marks survive refresh and page navigation; mark two segments of one song,
   unmark one, and confirm the other remains. Sign out and test the login prompt.
5. Audition two distinct segments rapidly, pause/resume, open a modal, navigate away,
   and retry after a download failure. Check actual browser audio and mobile layout.

## Local validation

- `npm run check-types`
- `npm run build`
- `npm run test:discovery` (search, real/demo recommendation filtering, segment history,
  playback races/cleanup/retry, auth identity, mark isolation, duplicate clicks and failures)
- `npm run test:catalog` (MIDI cache, catalog identity and local file handling)
- `npm run test:activity`, `npm run test:uploads`, `npm run test:protocol`

The migration has not been executed against a live database during this change.
Database RLS integration and actual browser audio require the deployment checks above.
