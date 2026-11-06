# 03: Liftosaur reading behind the contract

**What to build:** Move Liftosaur's read side behind the same contract Hevy uses. A Liftosaur user's pull, baseline report, deep-dive and weigh-in import produce byte-identical output on their existing store before and after. Analysis and check-in code then talk only to the contract, never to an app. The weigh-in import becomes app-neutral (`import-app`). The old guard test that forbade any mention of Hevy is replaced by a structural one: nothing outside the adapters may import an app-specific module or contain an app's base URL.

**Blocked by:** 02 (Hevy user gets a baseline report)

**Status:** ready-for-agent

- [x] Liftosaur declares and implements `ping`, `pull_history`, `pull_bodyweight` and `find_exercise` through the contract; its exercise translation is identity
- [ ] Pull, report and deep-dive output is byte-identical on the user's real Liftosaur store, compared before and after
- [x] The weigh-in import works for whichever app is active and still leaves hand-logged days alone
- [x] The structural import test passes and the Hevy-forbidding test is deleted
- [x] Switching the Active App replaces the cache on the next pull and leaves check-ins, the weight log and plans untouched
