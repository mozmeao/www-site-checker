---
name: review-social-links-in-allowlist-pr
description: Review a www-site-checker allowlist PR by decoding and translating percent-encoded URLs, then optionally approving and merging
argument-hint: [pr-number]
disable-model-invocation: true
---

PR body:
!`gh pr view $ARGUMENTS --json body --jq '.body'`

Review the allowlist PR above.

1. Parse the PR body for bullet-pointed URLs (lines starting with `* https://`). For each URL:
   - Extract the page path from the "Found in:" line immediately following
   - URL-decode the `text=` query parameter value
   - Identify the language (hint: the page path locale prefix like `/lo/`, `/sr/`, `/hi-IN/` etc.)
   - Provide a natural English translation of the decoded text

2. Group results by URL type — e.g. "Firefox Switch" (pages under `/firefox/switch/`) vs "Manifesto" (pages under `/about/manifesto/`).

3. Present a clean table or list like:

   **Firefox Switch URLs**
   | Locale | Decoded text | Translation |
   |--------|-------------|-------------|
   | /lo/   | 🔥 Firefox ເຮັດໃຫ້ການປ່ຽນຈາກ Chrome ໄວແທ້ໆ. ລອງໃຊ້ເບີ່ງ! | 🔥 Firefox makes switching from Chrome really fast. Give it a try! |

   **Manifesto URLs**
   | Locale | Decoded text | Translation |
   |--------|-------------|-------------|
   | /sr/   | Подржавам визију бољег и здравијег интернета... | I support the vision of a better and healthier internet... |

4. After displaying all results, ask: "Would you like to approve and squash-merge PR #$ARGUMENTS?"

5. If the user says yes, run:
   ```
   gh pr review $ARGUMENTS --approve && gh pr merge $ARGUMENTS --squash --delete-branch
   ```
