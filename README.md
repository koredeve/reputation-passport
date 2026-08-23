# Reputation Passport

A GenLayer smart contract for portable, AI-attested reputation. Any address registers a profile; peers leave 1-5 star reviews with free-form text about them; an AI validator panel judges whether each review is authentic or fake/incentivized, and only AUTHENTIC reviews feed a constant-time running average (sum and count per subject). The result is a reputation passport that travels with the address, not with any single platform.

## Architecture

- **User action**: An address calls `register_profile` to create its profile (unique per sender, non-empty display name plus links). Peers call `submit_review(review_id, subject, rating, text)` — the subject must be registered, ratings are 1-5, self-reviews and duplicate ids are rejected. Anyone calls `verify_review(review_id)` to run the authenticity check on a pending review.
- **Evidence source**: The on-chain review text and its rating (as x10/50) are the only evidence used for judging.
- **Nondet call**: `verify_review` runs a leader function that calls `gl.nondet.exec_prompt(..., response_format="json")` asking an LLM to assess the review and reply with JSON `{"authentic": bool, "confidence": 0-100, "reasoning": str}`.
- **Equivalence principle**: A custom validator reruns the identical leader prompt independently and accepts only on **exact agreement of the `authentic` boolean** between leader and validator reruns (confidence/reasoning are informational and not compared). Leader failures are reconciled via the canonical `_handle_leader_error` handler so deterministic `[EXPECTED]`/`[EXTERNAL]` errors reproduce consistently.
- **Settlement effect**: On `authentic`, the review's rating_x10 is folded into the subject's O(1) running average (`sum_rating_x10 += rating_x10`, `review_count += 1`) and the review becomes `authentic`; otherwise it becomes `fake` and stats stay untouched. Confidence and reasoning are persisted either way.
- **Appeal path**: GenLayer Optimistic Democracy provides leader-proposes / validator-check with an appeal window natively; no extra appeal logic is required at the contract level.

## Quickstart

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lint the contract
/Users/mac/Documents/Default\ Project/.venv/bin/genvm-lint check contracts/ReputationPassport.py --json

# Run direct-mode tests
/Users/mac/Documents/Default\ Project/.venv/bin/pytest tests/direct/ -v
```

## Interface

| Method | Type | Notes |
| --- | --- | --- |
| `owner()` | view | Contract owner (deployer), as string |
| `register_profile(name, links)` | write | One profile per sender; non-empty name |
| `submit_review(review_id, subject, rating, text)` | write | Subject must be registered; rating 1-5; no self-reviews; unique id; stored `pending` |
| `verify_review(review_id)` | write | Pending reviews only; AI judge marks `authentic` (feeds average) or `fake` |
| `reputation_of(who)` | view | `{avg_rating_x10, count}` from authenticated reviews only; 0 when unreviewed |
| `get_review(id)` | view | Review state incl. subject/reviewer keys, status, confidence, reasoning |
| `total_reviews()` | view | Number of submitted reviews |

## StudioNet

StudioNet is gasless — deploying and interacting costs 0 GEN.
