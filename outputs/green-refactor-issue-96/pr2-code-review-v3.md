No actionable correctness findings in the reviewed diff.

I inspected the tracked functional diff plus the two locally untracked files you called out, `tests/test_docs_monitor_workflow.py` and `metadata_cache.py`. The workflow filters now include the workflow contract test on both `push` and `pull_request`; the cache path is bounded, retry-aware, and isolated from count/freshness output; and the post-deploy smoke check validates enabled monitor routes against fresh manifest contracts.

I did not modify files or run tests.