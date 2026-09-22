# The Thousand Nights

> "I'll give you a program to follow every night, a very simple program. For the
> next thousand nights, before you go to bed every night, read one short story.
> That'll take you ten minutes, 15 minutes. Okay, then read one poem a night from
> the vast history of poetry... But one poem a night, one short story a night, one
> essay a night, for the next 1,000 nights. From various fields: archaeology,
> zoology, biology, all the great philosophers of time, comparing them. I want you
> to read essays in every field. On politics, analyzing literature, pick your own.
> But that means that every night then, before you go to bed, you're stuffing your
> head with one poem, one short story, one essay — at the end of a thousand nights,
> Jesus God, you'll be full of stuff, won't you?"
>
> — Ray Bradbury

## What this is

An automated run at Bradbury's program. A scheduled Routine fires once per day at
**12:00 PM US Eastern** and emails one poem, one short story, and one essay. Each
night is recorded in [`READING_LOG.md`](READING_LOG.md) so nothing repeats across
the full thousand.

## The rules the nightly run follows

**Three pieces, every night.** One poem, one short story, one essay. Never more,
never fewer.

**Breadth is the point.** Bradbury's insistence on "various fields" is the whole
engine. Essays rotate across archaeology, zoology, biology, physics, philosophy,
history, politics, mathematics, music, architecture, medicine, economics,
literary criticism, and anything else that qualifies — no field twice in a row,
and no field more than roughly once a fortnight.

**Range in time and place.** Poems reach from the Greek Anthology and the Tang
dynasty through Shakespeare, Pope, and Frost to living poets. Short stories span
Chekhov to Borges to now. The program is not a Western-canon tour.

**Full text where the law allows it.** Public-domain work (published before 1931
in the US, or otherwise free) is delivered complete — the reading should require
no second step. Work still in copyright gets a short excerpt, real context, and a
pointer to where to find it.

**A note, not a lecture.** Each piece carries two or three sentences on why it is
worth the ten minutes — what to watch for, what it is doing. Enough to open the
door, not enough to substitute for walking through it.

## Logistics

- Schedule: `0 16 * * *` (UTC), which is noon Eastern during daylight saving time.
  Eastern standard time shifts this to 11:00 AM; see the note below.
- Delivery: email.
- Log: `READING_LOG.md`, appended by each run, committed to this branch.

### Daylight saving

Cron here is evaluated in UTC and has no notion of US daylight saving. `0 16 * * *`
lands at noon Eastern from mid-March to early November and at 11:00 AM the rest of
the year. Changing the Routine's cron to `0 17 * * *` at the November transition
restores noon, and back to `0 16 * * *` in March.
