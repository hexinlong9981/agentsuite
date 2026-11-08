# playbook

A generic, business-agnostic set of operating principles for AI agents, as
structured data (`playbook.PRINCIPLES`), a rendered guide
(`docs/OPERATING_GUIDE.md`), and a light self-check.

```
playbook list | show <id> | render [-o FILE] | check | audit <file>
```

`audit <file>` reports which principles a guidelines document references (by id
or title), so a team can check its own agent guidelines cover them.
