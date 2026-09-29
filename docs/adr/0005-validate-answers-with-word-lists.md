# 0005. Validate answers with word lists, not a local model

## Status

Proposed

## Context

Scoring only checked the first letter, so `Elefnte`, `Asdf` or `Mesa` as an animal scored like real answers ([#30]). Validation has to run when a round closes, before scoring, for every answer that passes the letter check. It has to add at most a few seconds for 8 players (56 answers), work offline with `docker compose up` on a CPU, and never block a round when it fails.

We compared two kinds of validator on a labeled sample of 210 Spanish answers (`scripts/answer_validation_sample.csv`), 30 per category. Each category has 15 valid answers, common and uncommon, and 15 invalid ones: misspellings, words from another category, and gibberish. `scripts/answer_validation_spike.py` shuffles the sample into rounds of 56 unique answers and times each pipeline per round. Unverified answers count as accepted, as they are in the game.

- **Word lists**, built by `scripts/build_lexicons.py` into `core/lexicons/`:
  - Countries: CLDR Spanish territory names from Babel, plus common variants (Holanda, Inglaterra, Qatar).
  - Cities: GeoNames `cities15000` names, plus the Latin-script alternate names of cities over a million people (Londres, Nueva York).
  - First names and surnames: INE frequency lists, names held by at least 50 people and surnames by at least 100.
  - Animals and colors: curated by hand.
  - For things there is no list. A dictionary of Spanish words with a wordfreq Zipf frequency of at least 2.5 checks spelling only.
- **Local models** served by Ollama 0.34.4 on CPU, asked for a strict JSON verdict per answer in one request per round: qwen2.5 0.5B, 1.5B and 3B, gemma2 2B and llama3.2 3B. Each ran alone on the round, and after the lists (as a fallback for answers the lists don't cover).

The spike ran on an 8-core Intel Core Ultra 5 238V laptop (WSL2, Docker Desktop), with llama.cpp limited to 4 threads. The machine was also running other test suites, so the model latencies are pessimistic. Even at several times faster, they miss the budget.

| Pipeline | Accuracy | False rejects | False accepts | p50 | p95 | Model RAM |
|---|---|---|---|---|---|---|
| Lists; misses left unverified, except countries | 57% | 1% | 86% | <0.01 s | 0.2 s¹ | - |
| Lists + dictionary for things | 60% | 3% | 77% | <0.01 s | 0.1 s¹ | - |
| **Lists that reject misses + dictionary for things** | **90%** | **9%** | **10%** | **<0.01 s** | **<0.01 s** | - |
| Lists that reject misses for names, surnames, countries and cities + dictionary for the rest | 85% | 6% | 24% | <0.01 s | <0.01 s | - |
| qwen2.5:0.5b | 50% | 0% | 100% | 7.0 s | 7.6 s | 0.5 GB |
| qwen2.5:1.5b | 52% | 95% | 1% | 15.6 s | 17.8 s | 1.2 GB |
| gemma2:2b | 68% | 43% | 21% | 27.0 s | 39.0 s | 1.9 GB |
| qwen2.5:3b | 87% | 14% | 12% | 35.6 s | 47.7 s | 2.2 GB |
| llama3.2:3b | 82% | 18% | 18% | 32.1 s | 35.4 s | 2.6 GB |
| Lists + dictionary + qwen2.5:0.5b | 72% | 8% | 49% | 2.5 s | 3.4 s | 0.5 GB |
| Lists + dictionary + qwen2.5:1.5b | 90% | 9% | 11% | 5.9 s | 16.4 s | 1.2 GB |
| Lists + dictionary + gemma2:2b | 89% | 6% | 16% | 13.7 s | 23.3 s | 1.9 GB |
| Lists + dictionary + qwen2.5:3b | 90% | 5% | 16% | 13.9 s | 20.2 s | 2.2 GB |
| Lists + dictionary + llama3.2:3b | 88% | 4% | 21% | 13.0 s | 16.2 s | 2.6 GB |

¹ The first round of a process loads the lists.

The deterministic rows are over 12 rounds and the model rows over 4. The strict lists' remaining false rejects were mostly animals missing from the hand-made list (wombat, kiwi, yacaré, pez espada). The animal list then grew from 211 to 401 entries, and Qatar and a few compound colors were added. With those lists, the chosen pipeline scores 94% with 2% false rejects and 10% false accepts. That run is biased: the additions came from reading the sample's misses.

## Decision

1. **Round close validates, then scores.** `PartyStateMachine.update_scores` calls `answer_validation.avalidate_round`, which collects the round's unique `(category, normalized value)` pairs that pass the letter check. `close_round_and_calculate_scores(verdicts)` then scores:
   - A rejected answer scores 0 and doesn't share points.
   - Accepted and unverified answers score as before.
   - `UserRoundAnswer.verdict` stores `valid`, `invalid` or `unverified`, and NULL for answers that failed the letter check or are empty.
   - The answer-reveal modal strikes rejected answers through and marks them "No válida".
2. **Validators form a chain**, set by the `ANSWER_VALIDATORS` setting (comma-separated in the environment). Each validator returns verdicts for the pairs it can decide and leaves the rest to the next one. The default chain has no model:
   - `LexiconValidator` accepts listed words and rejects unlisted ones in every category that has a list.
   - `DictionaryValidator` accepts things spelled as common Spanish words and rejects the rest.
3. **Stored verdicts come first.** `AnswerVerdict(field, value, is_valid, source)` is checked before any validator, so an admin can accept or reject a word by hand. Verdicts from validators that set `cache_verdicts` (the model) are stored there too.
4. **Failures don't block.** Validators run off the event loop (`sync_to_async(thread_sensitive=False)`) under one deadline, `ANSWER_VALIDATION_TIMEOUT` (5 s). On timeout, the verdicts already found are kept and the rest stay unverified. A validator that raises is skipped. Unverified answers are accepted.
5. **The model stays optional.** `OllamaValidator` is kept for hardware that can run it in time. `docker compose --profile llm up` starts `ollama` and pulls `OLLAMA_MODEL` (qwen2.5:1.5b by default) into the `ollama` volume. To use it, put `PartialLexiconValidator` first, which only rejects unlisted countries:

   ```
   ANSWER_VALIDATORS=core.answer_validation.PartialLexiconValidator,core.answer_validation.DictionaryValidator,core.answer_validation.OllamaValidator
   ```

### Alternatives considered

- **A local model as the main validator.** It wasn't more accurate than the lists, and it took 6 to 48 s per round on CPU and 0.5 to 2.6 GB of RAM. The small models either accepted almost everything or rejected almost everything.
- **Lists that leave misses unverified.** Instant and never wrong about listed words, but 77–86% of invalid answers got through.
- **The dictionary for animals and colors too.** Fewer false rejects, but any real word passes as an animal or a color. It let 24% of invalid answers through.
- **Fuzzy matching** (e.g. rapidfuzz) to forgive typos. The rule is that answers are spelled correctly, so typos are rejected on purpose.

## Consequences

- Validation takes under a millisecond per round and needs no extra service. Each worker loads the lists once, which takes about 0.2 s and 16 MB of RAM.
- `core/lexicons/` is 1.6 MB of plain text. Countries, cities, names, surnames and the dictionary are generated, so rebuild them with the script instead of editing them. The animal and color lists are edited by hand.
- A correct answer that isn't listed is rejected: an uncommon animal, a small town, a rare name. The fix is to add it to a hand-made list, or to accept it in the admin through `AnswerVerdict`. Letting players challenge a verdict would make this self-service.
- A word from another category passes for things (`Perro` as a thing), because the dictionary only checks spelling. Place names shared with common words pass as cities (`Mesa`, `Colombia`).
- The name and surname lists come from Spain's census, so Latin American names that are rare in Spain can be rejected.
- Tests use fake validators, and the test settings set `ANSWER_VALIDATORS = []`, so CI needs no model and no lists beyond the repository.

[#30]: https://github.com/DanielGnzlzVll/AACX/issues/30
