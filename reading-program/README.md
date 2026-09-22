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

An automated run at Bradbury's program. A scheduled Routine fires once per night at
**11:30 PM US Eastern** and writes one poem, one short story, and one essay onto a
private reading page:

**https://claude.ai/artifact/RjtHMetrGCGWxkYgM41XPN**

The page is also the program's memory. It carries the running log of every night,
so nothing repeats across the full thousand, and the last thirty nights stay
readable in an archive below the current one.

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

**The poem whole, the prose opened.** A poem in fragments is nothing, so poems go
in complete. Short stories and essays get their opening two or three paragraphs —
enough to establish a voice and pull you in — then a pointer to where the rest
lives. That is a deliberate trade: full texts every night cost about a dollar a
run, roughly a thousand dollars across the program.

**Public domain by preference.** Work published before 1931 in the US, or
otherwise free, so excerpts are safe and the links go somewhere public. Work still
in copyright gets a few lines at most and a pointer.

**A note, not a lecture.** Each piece carries two or three sentences on why it is
worth the ten minutes — what to watch for, what it is doing. Enough to open the
door, not enough to substitute for walking through it.

## Logistics

- Schedule: `30 3 * * *` (UTC), which is 11:30 PM Eastern during daylight saving
  time. Because 11:30 PM Eastern falls after midnight UTC, each run is stamped with
  the following day's UTC date.
- Delivery: the reading page above, republished in place each night.
- Log: the page's own log table. `READING_LOG.md` in this directory records why
  the log lives there rather than here.

### Daylight saving

Cron here is evaluated in UTC and has no notion of US daylight saving. `30 3 * * *`
lands at 11:30 PM Eastern from mid-March to early November and at 10:30 PM the rest
of the year. Changing the Routine's cron to `30 4 * * *` at the November transition
restores 11:30 PM, and back to `30 3 * * *` in March.
