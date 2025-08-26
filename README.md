# Minimum personal assistant: Shiye (师爷)

> a baby step a day, or maybe every two, three, four... days...

## Current status

### Dev run

![](./screenshot.svg)

The ongoing project:

```bash
python main.py
```

What it does:

- multi-round chat with DS backend
- Text UI
- add stuff to the context, sorta in context learning
- summarize the context
- enhanced timeline processing
- multiple copy input input box

## End Goal

Functions-wise, what's most important to me, for my personal uses:

- a personal off-brain data scope: such that I may pile up my readings, messages, thoughts here
- auto & dynamic organization / structurization / focusing for on going discussion, logged exchanges, or data
  - i.e. my input or exchange to the tool is linear as if I have an infitie long context
  - yet, the info processing capabilitiy of LLM should organize things and distill insights as we go
- proactively suggest actions

## Next bady steps

- [x]: refactor the mvp in a more modular layout
- scope on certain in-context logs by semantic, time cues
  - [x] modify the log data structure, ask LLM to break down into time atomic pieces
  - [x] for each piece two time stamps, one for creation, one for event
  - [ ] pick a benchmark and SOTA on tineline reasoning, maybe https://huggingface.co/papers/2505.12891
  - [ ] how to test the in context log scoping ?
- save logs on scoping
- clear logs on scoping
- stash away, save and then clear on scoping
- summarize and then stash, summary in context, logs saved out
- scope external logs by semantic, time cues
- load stashed logs back
- auto stash, summarize, load back
