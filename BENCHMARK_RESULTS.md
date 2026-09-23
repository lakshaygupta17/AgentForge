# AgentForge measured benchmark results

## Budgeted 20-case SWE-bench Verified run — September 14, 2026

AgentForge completed a frozen, non-cherry-picked 20-case subset spanning ten
repositories. The manifest takes the first two previously selected cases from
each repository while preserving the earlier balanced 100-case order. Cases ran
in four sequential waves of five E2B microVMs. Canonical grading then applied
the generated patches and the official hidden test patches in fresh E2B
microVMs.

| Measurement | Result |
| --- | --- |
| Canonical SWE-bench Verified result | 9/20 resolved (45.0%) |
| Canonical grader errors | 0/20 |
| Repositories represented / resolved | 10 / 6 |
| Hidden FAIL_TO_PASS tests on resolved cases | 18/18 passed |
| PASS_TO_PASS regression tests on resolved cases | 508/508 passed |
| Non-empty generated patches | 14/20 |
| Successful patches among non-empty predictions | 9/14 (64.3%) |
| Provider requests | 323, including two preflight calls |
| Provider-reported tokens | 4,351,430 |
| Estimated inference cost | $0.89365 total; $0.04468 per selected case |
| Authorized global inference cap | $2.50; 35.7% used |
| Per-case fail-closed cap | $0.125 and 500,000 tokens |
| Cases stopped by the per-case token cap | 7/20 |
| Generation concurrency | Four sequential waves of at most 5 |
| Active E2B sandboxes after cleanup | 0 |

DeepSeek V4.1 Flash handled Planner, Repository Explorer, Developer, Debugger,
and Reviewer. Qwen3.6 was the preferred Tester with transient fallback to
DeepSeek. The internal workflow accepted only one patch, while canonical grading
resolved nine. That difference shows the export gate is conservative and needs
better calibration; it does not change the canonical 9/20 result. Seven cases
were stopped when their local token reservation would have crossed 500,000, so
an expensive case could not consume the $2.50 run budget.

This is a custom frozen subset result, not an official SWE-bench leaderboard
submission. The selection manifest is `benchmarks/swebench-cv-20.txt`; complete
generation and grading evidence is in
`artifacts/swebench-cv-20-resilient-hybrid`. The generation summary records
`$0.8936502`; the slightly shorter table value is rounded, not provider billing.

### Targeted sequential retry of token-capped cases

Five of the seven cases stopped by the first-pass token limit were still
canonically unresolved. The other two had already resolved despite their later
workflow stop, so they were not rerun. The five useful cases were frozen in
`benchmarks/swebench-cv-expensive-5.txt` and retried one at a time with an
effectively non-binding 10-million-token ceiling. Dollar, request, response, and
policy limits remained active.

| Measurement | Result |
| --- | --- |
| Retry cases | 5, sequential |
| Canonical retry result | 1/5 resolved; 0 grader errors |
| Newly resolved case | `pydata__xarray-3305` |
| New case's canonical tests | 1/1 FAIL_TO_PASS; 653/653 PASS_TO_PASS |
| Retry provider requests / tokens | 97 / 2,681,126 |
| Retry estimated inference | $0.54307 |
| Retry inference cap | $1.60 global; $0.32 per case |
| Highest completed case usage | 905,102 tokens |
| Active E2B sandboxes after cleanup | 0 |
| Best-of-first-pass-and-retry result | 10/20 resolved (50.0%) |
| Combined resolved-case tests | 19/19 target; 1,161/1,161 regression |
| Cumulative inference | $1.43672 of $2.50 (57.5%) |

The 50% figure is explicitly a retry-assisted best-of-two-pass result, not a
first-attempt solve rate. The first-pass result remains 9/20 (45%). Four retries
remained unresolved; one reached its $0.32 local cost reservation boundary. The
new Xarray prediction is stored separately in
`artifacts/swebench-cv-expensive-5-retry`, preserving the original run.

## SWE-bench Verified smoke and partial 100-case run — September 13, 2026

The real-repository adapter was validated end to end on
`astropy__astropy-12907`. AgentForge produced a standard Git patch, and the
official E2B harness applied and graded it in a separate fresh microVM. The
patch did not resolve the held-out tests, so the canonical smoke score is
`0/1`. This is a valid infrastructure smoke result, not evidence of repair
quality.

The approved 100-case generation used a balanced selection spanning 12
repositories. Provider credits were exhausted during generation. The resumable
controller recorded every selected case, but 77 are provider failures rather
than repair outcomes, so the run is incomplete and cannot produce a valid
100-case success rate.

| Measurement | Result |
| --- | --- |
| Cases with genuine repair outcomes | 23/100 |
| Internally accepted workflows | 8/23 |
| Internally unresolved workflows | 15/23 |
| Cases ending in provider HTTP 402 | 77 |
| Provider-reported tokens | 19,530,171 |
| Successful model responses | 1,185 |
| Repair attempts | 60 |
| Canonically graded full-run cases | 0/100 (not yet run) |
| Active E2B sandboxes after cleanup | 0 |

Internal acceptance is not a SWE-bench score. Only `swebench-grade` can produce
the held-out resolved rate. The results and prediction parts are preserved in
`artifacts/swebench-agentforge-100`. Provider failures must be retried under a
new budgeted run before full canonical grading is meaningful.

## Budgeted hybrid smoke — September 14, 2026

AgentForge routed Qwen3.6 to Planner, Repository Explorer, and Tester, and
DeepSeek V4.1 Flash to Developer, Debugger, and Reviewer. Four deliberately
capped runs used the same `pytest-dev__pytest-10051` case. No run was locally
accepted, so this experiment measures compatibility, failure modes, and cost—not
repair success.

| Measurement | Result |
| --- | --- |
| Runs | 4, sequential and independently capped |
| Provider-reported tokens | 672,248 total |
| Estimated model cost | $0.0879 total |
| Maximum simultaneous E2B sandboxes | 1 |
| Runs reaching both models | 1/4 |
| Visible test result after DeepSeek patch | Exit code 0 |
| Final accepted workflows | 0/4 |
| Best post-hardening exploration | 47,319 tokens, $0.00795 estimated |
| Comparable pre-hardening exploration | 264,330 tokens, $0.0309 estimated |

The first run failed because Qwen's final Tester report contradicted the
successful machine result. A bounded report correction was added. Subsequent
runs showed that Qwen could repeatedly read the same file and include nonexistent
issue-mentioned test paths in `relevant_files`. Duplicate requests are now
rejected, early finalization is enforced, and invalid file reports receive one
bounded correction. Those changes improved efficiency substantially, but Qwen
still failed the grounding gate on this case. DeepSeek's patch is therefore not
reported as a completed repair.

### DeepSeek Explorer follow-up

Repository Explorer was then moved from Qwen to DeepSeek while Qwen retained
Planner and Tester. Two controlled retries of the same case each stopped at the
first Qwen Planner call after a read timeout, before Explorer could start. Each
run recorded one request, zero provider-reported tokens, and $0 estimated usage.
Both E2B microVMs were cleaned up, leaving zero active sandboxes. The wiring is
covered by the passing local suite, but these attempts provide no evidence yet
about DeepSeek's Explorer quality and are not counted as repair attempts.

To test the role despite the upstream Qwen timeout, DeepSeek Explorer was run
directly against the synthetic checkout fixture with no E2B microVM. It returned
a valid grounded report after four requests, 9,969 provider-reported tokens, and
$0.00237 estimated inference. The report included all six inspected relevant
paths and correctly identified the seeded discount-rounding, tax-exemption, and
discounted-subtotal free-shipping defects. These findings were checked against
the source and acceptance tests. This is a successful Explorer-role test, not an
end-to-end repair or SWE-bench score.

## Resilient hybrid canonical success — September 14, 2026

After moving Planner to DeepSeek, adding Qwen-to-DeepSeek Tester fallback,
grounded Explorer correction, and early Developer finalization, AgentForge
completed `pytest-dev__pytest-10051`. Canonical grading ran in a fresh E2B
microVM using the official hidden test patch and marked the case resolved.

| Measurement | Result |
| --- | --- |
| Canonical SWE-bench result | 1/1 resolved |
| Hidden FAIL_TO_PASS tests | 1/1 passed |
| PASS_TO_PASS regression tests | 15/15 passed |
| Visible focused suite | 16/16 passed |
| Provider preflight | Qwen and DeepSeek ready |
| DeepSeek roles | Planner, Explorer, Developer, Reviewer |
| Qwen roles | Tester |
| Model requests | 19, including two preflight calls |
| Provider-reported tokens | 125,593 |
| Estimated inference cost | $0.02625 |
| Workflow duration | 192.08 seconds |
| Changed files | 2 |
| Maximum simultaneous E2B sandboxes | 1 |
| Active E2B sandboxes after cleanup | 0 |

The generated repair preserved the shared log-record list across `caplog.clear()`
and added a focused regression test. This establishes one canonical repair and
the complete hybrid path; it must not be presented as a broader benchmark rate.

## Cloud billing repair — September 13, 2026

A controlled, synthetic six-file Python billing repository contained six seeded
defects across three implementation modules: proration rounding, graduated usage
pricing, coupon rounding, credit capping, taxable-base ordering, and exemption.
The reference implementation passed the acceptance tests before defects were seeded.

| Measurement | Result |
| --- | --- |
| Baseline acceptance tests | 4 passed, 9 failed (13 total) |
| Repaired acceptance tests | 13 passed, 0 failed |
| Seeded defects addressed | 6 across 3 implementation modules |
| Successful workflow duration | 66.98 seconds |
| Repair attempts within successful workflow | 1 |
| Accepted patch operations | 3 |
| Model requests | 19 |
| Provider-reported tokens | 51,012 |
| Model | qwen/qwen3-coder-480b-a35b-instruct via Hugging Face |
| Execution backend | E2B Hobby microVM |
| Export | 6 files; tests, task brief, and models byte-identical to input |
| Independent verification | All 13 exported tests passed locally |
| Final review | Accepted, zero reported issues |

Run ID: `bc627ee3-4088-4bb0-b703-6451248fec03`.
Local trajectory: `trajectories/live-repair-20260913T131459Z.jsonl`.
Local output: `artifacts/cloud-billing-repaired`.
These generated artifacts are intentionally ignored by Git.

Duration is measured between workflow events; it excludes VM startup/export and
the time spent developing fixes. This is one successful controlled benchmark,
not a general repair success rate or a production workload measurement.

Earlier cloud-billing runs are retained as failures: a Tester report protocol
failure, a provider timeout, and a malformed Reviewer report. The final run used
bounded report recovery and an explicit unittest command in the task. It is not
a first-run success claim. No dollar-cost estimate is claimed.

## Multi-warehouse fulfillment stress test — September 13, 2026

This deliberately harder eight-file fixture combines proportional promotion
allocation, FEFO inventory reservation, dimensional-weight shipping, aggregate
tax, store credit, and settlement ordering. Its defects cross five implementation
modules. An independently checked reference version passes all 23 tests.

| Measurement | Result |
| --- | --- |
| Baseline acceptance tests | 5 passed, 18 failed (23 total) |
| Final AgentForge acceptance tests | 20 passed, 3 failed |
| Initially failing checks repaired | 15 of 18 (83.3%) |
| Pass-rate change | 21.7% to 87.0% (+65.2 percentage points) |
| Repair attempts | 5 |
| E2B test runs | 5 |
| Validated patch operations | 11 |
| Workflow duration | 260.88 seconds |
| Model requests | 73 |
| Provider-reported tokens | 286,452 |
| Model | qwen/qwen3-coder-480b-a35b-instruct via Hugging Face |
| Execution backend | E2B Hobby microVM |
| Final state | Failed closed; no repaired output exported |

Run ID: `1886d63c-302f-48a4-aafb-23e9354bf3c4`.
Local trajectory: `trajectories/live-repair-20260913T135311Z.jsonl`.

An earlier version reached 22/22 tests, but independent review found that the
generated shipping code used floating-point division despite an integer-only
rule. That result is deliberately excluded. A large-integer precision case was
added, producing the stricter 23-test contract above. Under that contract the
remaining failures concern settlement totals, shortage signaling, and store-credit
application. This is a partial-repair stress result, not a successful benchmark.

## Resume wording

Built a budget-aware six-agent repair system that resolved 9/20 (45%) cases on
a frozen SWE-bench Verified subset across ten repositories, passing all 18
target and 508 regression tests on resolved cases for $0.89 estimated inference.

Improved the frozen subset to 10/20 resolved (50%) through five targeted,
sequential retries, with all 19 target and 1,161 regression tests passing across
resolved cases for $1.44 cumulative estimated inference; first-pass score was 45%.

Built a six-role Python coding-agent workflow with policy-controlled E2B execution,
validated patching, debugging retries, and structured review; repaired six seeded
billing defects across three modules in a 67-second successful live benchmark,
improving acceptance results from 4/13 to 13/13 without modifying tests.

Validated the project with 210 passing offline tests and 20 passing subtests;
three external integration tests are opt-in. Ruff and strict MyPy pass across
69 application source files.

Stress-tested AgentForge on an eight-file, five-module fulfillment repair: it
resolved 15 of 18 initially failing acceptance checks in five E2B repair cycles,
raising the pass rate from 21.7% to 87.0%, then failed closed without exporting
an incomplete repair.
