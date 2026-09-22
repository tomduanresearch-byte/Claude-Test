# Reading Log

**The log lives on the reading page, not in this file:**
<https://claude.ai/artifact/RjtHMetrGCGWxkYgM41XPN>

## Why

The first design kept the log here and had each nightly run commit a row. That
does not work, and the failure is worth recording so nobody rebuilds it.

A Routine that starts a fresh session per firing gets a narrow toolset — Bash,
file tools, WebFetch, WebSearch, Agent and Artifact — with **no GitHub tools and
no repository checked out**. It cannot call `add_repo` or `get_file_contents`, and
a plain HTTPS clone could not be verified either: the permission classifier
declines credential paths from these sessions. The Night 1 run proved it the
expensive way — it finished successfully, generated 42,103 output tokens of
reading, and wrote nothing at all to this file.

A second wrong assumption went with it. A Routine's email setting sends a
*notification summary* when a run finishes, not the run's final message. It was
never going to carry a poem, a story and an essay. No email arrived for Night 1.

So both the reading and the log moved to a published Artifact page, which the
nightly session reaches with the `Artifact` tool it provably has — read the page,
append the night, republish to the same URL. No credentials, nothing to clone.
The page's own HTML carries the update protocol the nightly run follows.
