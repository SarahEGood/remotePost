# RemotePost

A personal tool for me to schedule posts to my Bluesky account.

## Credentials format

`credentials.txt` should contain repeating three-line blocks:

1. account label
2. Bluesky handle
3. app password

Example:

```text
default
example.bsky.social
xxxx-yyyy-zzzz-1234
alt-account
other-account.bsky.social
aaaa-bbbb-cccc-5678
```

## Planned features

* Read and reply to notifications (such as replies and DMs)
* Expand to other social media platforms

## Scheduled delivery lifecycle

Scheduled work is stored as platform-specific deliveries with:

* a target account label
* an immutable payload snapshot
* delivery states of `pending`, `delivering`, `delivered`, `failed`, or `unknown_outcome`
* automatic retries for retryable failures
* delivery receipts for successful publications
