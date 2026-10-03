# Weekly update (one minute)

Narration in the founder's own voice, about 150 words, over one screen recording. The number in the second part is
a slot, `[[stat: name]]`, filled from the release run.

## What shipped (0:00)

*On screen: a comment funds an issue, a merge, the payment in the explorer.*

This week I shipped the second deployment of Knos. A maintainer funds an issue with one comment and names the
checks that must pass. Those terms are fixed on chain. When she merges a pull request that meets them, her workflow
reads the merge and the checks from GitHub, GitHub signs that run, a Solana program checks the signature, and the
author is paid straight to a wallet. There is no veto after the
merge.

## One number (0:25)

*On screen: the site's Numbers page.*

From merge to paid on devnet: 110 seconds at the median.

## The hardest problem, and the decision (0:30)

*On screen: the table at the top of CHANGELOG.md.*

Three outside reviews found faults in the programs I had already made immutable. The worst: a funder could merge
and then take the money back. I could not patch them. So I said so in public, left the first deployment as it is,
and deployed a second one that stays changeable, behind a public 48-hour delay, until someone outside has
reviewed it.

## Next week (0:55)

One repository that is not mine funds a task and pays someone.
