# What this project is about (no technical background needed)

## The problem

Companies change their IT systems all the time: installing updates, releasing new versions of a website, changing
firewall rules, upgrading databases. Each of these is a **change**. Before a change is made, it is written up as a
**change request** in a tool such as **ServiceNow**, and someone has to decide whether it is safe to go ahead.

Badly controlled changes are one of the biggest causes of IT outages. A routine update on the evening before a big
sale can take an online shop down when customers need it most.

Today, deciding is mostly manual work:

1. **Read every request.** Is the plan clear? Is there a way to undo it if it goes wrong? Is there a plan to test it?
2. **Judge the risk.** The person asking for the change usually rates their own risk, and often rates it too low.
3. **Check the calendar.** Does it clash with other changes, or fall in a "freeze" (for example a sale weekend, when
   no changes are allowed)?
4. **Check afterwards.** Did the change cause problems? This step is often skipped, and a change gets marked
   "successful" even when it broke something the next morning.

A **change advisory board (CAB)** of senior staff reviews the risky ones. Their time is limited, so they need the
right changes to look at, with the evidence ready.

## What this project does

It shows a team of AI **agents** preparing each change request for that decision, and checking each change after
it is made. **People still make every decision.** Each agent is a small program with one job:

| Agent | What it does, in everyday terms |
|---|---|
| Quality | Checks that the plan, the undo plan and the test plan are there and actually say something |
| Impact | Works out what else would break if this system broke, and how important those systems are |
| History | Looks at how similar changes went in the past, and whether the system already has open problems |
| Schedule | Checks maintenance windows, business hours, sale freezes and clashes with other planned changes |
| Risk | Estimates how likely the change is to fail, and recommends: approve, approve with conditions, send to the board, or send back |
| Verification | After the change: did it cause incidents? Suggests whether to close it as successful or not |

The Risk agent **learns from experience**. It studied 260 past changes and what happened after each one, and worked
out which warning signs really mattered.

## The safety rules

- **The AI only recommends.** It writes a note and its own risk rating on the request. It never approves a change,
  never moves it forward and never closes it.
- **When unsure, it escalates.** If the evidence is thin, it errs towards sending a change for review. Waving a risky
  change through costs far more than one extra review.
- **Every recommendation shows its reasons.** For example: "planned during business hours", "3 of 8 similar changes
  caused problems", "clashes with another change on the same database".
- **It follows the company's published change policy.** No rules are hidden.

## What it demonstrates

I tested it on **40 change requests waiting for a decision** and **20 changes already carried out**. The correct
answers were known in advance and hidden from the AI.

| Question | Rules only | With Claude reading the text |
|---|---|---|
| High-risk changes waved through without review | 1 of 12 | **0 of 12** |
| Recommendations matching the right answer | 65% | 78% |
| Missing or vague plans spotted | all of them | all of them |
| Clashes with other changes and sale freezes spotted | all of them | all of them |
| Changes that caused problems, caught afterwards | **8 of 8** | **8 of 8** |
| Changes that went fine, but were wrongly flagged | 2 of 12 | 2 of 12 |

**The most interesting finding** is the one high-risk change it missed. That change, like the others it found
hardest, had its risk hidden in the wording rather than in the form fields. One example: "the old columns are
deleted and their data is not copied anywhere". In plain words, there is no way back. Rules learned from past
records struggle with this, so I let **Claude**, a language model, do just that one job: read each request and
point out warning signs, quoting the exact words as proof. With that, the tool missed no high-risk change, and its
recommendations matched the right answer 78% of the time instead of 65%. Reading all 300 requests cost under one
US dollar.

It also ran against a **real ServiceNow system** with the same data loaded (about 900 records) and made exactly the
same recommendations as the offline test. It wrote its assessment as a note on each of the 60 change requests,
where a change manager would see it ([screenshots](live-pdi.md)).

## What is real and what is made up

- **Real:** the ServiceNow system, the agents, the learning from history, and the scoring.
- **Made up:** the company. *ShopKart Retails* is a fictional retailer, and its staff, systems, change requests and
  history are generated test data. No real client or employer information is used. This is the same fictional
  company as in my earlier [incident project](https://github.com/asampath1/agentic-aiops-servicenow-azure).

## Honest limits

- The test data comes from a model I wrote, so real-world accuracy would be lower. Real change requests are messier.
- The tool is deliberately cautious. It rates many changes one level riskier than they really are (with Claude:
  7 of 13 low-risk changes and 6 of 15 medium-risk ones), so the board sees more than strictly necessary. That is a choice,
  and it can be dialled down.
- Claude was tested in one run. A language model's answers can vary a little from run to run.

## Why it matters

For a business, this means fewer outages caused by its own changes, a review board that spends its time on the
changes that need it, and an honest record of which changes caused trouble. Because a person makes every decision and
every recommendation shows its evidence, it is safe enough to trust.

---

For more detail: [project README](../README.md) · [how accuracy is measured](../evaluation/README.md) ·
[design](design.md)
