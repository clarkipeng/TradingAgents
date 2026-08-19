# Local temporal scheduler

The scheduler is intentionally local-first. It runs the same supported capture
command on a fixed, reviewable universe and writes only to the temporal corpus.
It does not deploy the Fly poller or create a cloud resource.

## macOS launchd

Choose an absolute corpus directory, then run once:

```sh
scripts/install_temporal_launchd.sh /absolute/path/to/temporal-corpus
```

The installer writes a per-user launchd job for 17:15 local time on weekdays.
It skips weekends, invokes `temporal-capture --full-surface`, and logs under
`.tradingagents/`. Edit [config/temporal-universe.txt](../config/temporal-universe.txt)
to change the default 20–50-symbol universe. Run the capture script directly to
test it before installing:

```sh
TRADINGAGENTS_TEMPORAL_STORE=/absolute/path/to/temporal-corpus \
scripts/run_temporal_capture.sh
```

## cron or another scheduler

Set `TRADINGAGENTS_TEMPORAL_STORE` and invoke
`scripts/run_temporal_capture.sh` once after the local market close. The script
has no daemon state; the temporal store makes each source observation immutable.
Inspect the command’s exit status and logs. A nonzero capture is deliberately
visible rather than silently treating a missing source as a successful corpus.
