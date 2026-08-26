# CLI guide: the source audit

The dated research record behind [`cli-guide.md`](cli-guide.md). Four
published CLI guides were walked one guideline at a time on
2026-08-24, for #285, and every guideline in each source was given a
disposition from a fixed vocabulary of eight words. What follows is
that walk as it was recorded: the sources are as they read on that
date, and the dispositions are the ones taken then.

This is evidence, not standard. The guide outranks it for current
practice: where a row and the guide disagree about what vinga does
today, the guide is right and this record says what was true when it
was written, and no row is edited into agreement with a later re-cut.
Where a row says "above" or "below" it means the practices in
[`cli-guide.md`](cli-guide.md), which is where this walk was written
and where each disposition's reasoning lives. The guide keeps the
short version in
[The sources, and what became of them](cli-guide.md#the-sources-and-what-became-of-them).

## The audit record

Four published guides, walked one guideline at a time. Every guideline
in each source has a row, so the coverage is checkable rather than
asserted. Where two sources say the same thing, the second names the
practice the first was dispositioned into rather than repeating the
reasoning.

Every row's disposition is exactly one of these eight words, and the
Where column carries the qualification. That is a property worth
keeping mechanical: a reader can check the vocabulary by scanning one
column, and a row that needed a ninth word is a row that has not been
thought through.

- **Adopted.** The rule holds as written.
- **Adapted.** The rule holds in a modified form, and the row says how.
- **Owed.** Adopted as the standard, not met by the grammar today.
- **Rejected.** Deliberately not followed, and the row says why.
- **Deferred.** Not rejected and not owed: a decision held open on
  purpose, with the case for reopening it written down. Three rows,
  all of them `--json`.
- **Split.** One guideline that the source states as one and this
  project answers in parts, because the parts have different answers.
  The row names each part and its disposition. It is not a hedge:
  "Split" with an unexplained Where column would be one.
- **Tension recorded.** The merged code contradicts a rule this page
  states, and the contradiction is written down rather than argued
  away. No row carries it today: the one that did (clig 61) and the two
  tensions in the practices above were all closed by #289, #290 and the
  #223 re-cut. The word stays in the vocabulary because the next
  contradiction wants a name, and because a page with nowhere to record
  one would argue it away instead.
- **N/A.** Out of scope for a configuration CLI.

### ThoughtWorks, "Elevate developer experiences with CLI design guidelines"

Eight guidelines, in ten rows: the sixth bundles three separate rules
under one heading and they are dispositioned separately, as 6a, 6b and
6c.

| # | Guideline | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Be consistent in structure and follow common naming; `platform-cli [noun] [verb]` | Adopted | Noun first, since the #223 re-cut |
| 2 | Prompt if you can, but never mandate; confirmation prompts for critical actions; a force flag | Adopted | Prompt where there is somebody to ask, never require it; a destructive verb confirms at a terminal and `--force` answers it |
| 3 | Use expressive flags; one argument fine, two questionable, three never | Adapted | Identity addressing: the cap applies to identity segments, not to flags |
| 4 | Avoid implicit steps; inform or split the command | Adopted | A write says what it did and when it takes effect; `apply` never deletes; a write never reloads by itself |
| 5 | Always provide help: command, arguments and flags described, examples most read | Split | Adopted: the whole tree's help is the committed reference, every command, argument and flag described. Owed: examples, which are the recipes region rather than the command pages |
| 6a | Exit nonzero if and only if the program terminated with errors | Adopted | One sentence and exit 1 |
| 6b | stdout for information and warnings, stderr for errors | Adapted | Data on stdout, notices on stderr: warnings and notices go to stderr here, following the other three sources, because a notice must survive `export > file` |
| 6c | Error messages carry an error code, title, description, resolution steps and a URL | Rejected | The contract is one fixed sentence carrying the fix. A code is a second vocabulary to keep honest; a URL dates and cannot be reached from a private deployment; and the five-part format invites quoting the input back, which is the one thing these sentences exist not to do |
| 7 | Keep the user in the loop: current step, long-run indicator, OS notifications | Split | Adopted: `apply` prints one line per entry as it reports them. Owed: the progress line. Rejected: OS notifications, a desktop assumption a container CLI does not have |
| 8 | Be fun and fancy: color, spinners, tables, machine-readable output | Split | Tables and machine-readable output adopted; color, ASCII art and emoji rejected under output determinism |

### clig.dev, "Command Line Interface Guidelines"

Ninety-six guidelines, in the document's own section order.

| # | Guideline | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Use a command-line argument parsing library | Adopted | Typer over Click, driven with standalone mode off so refusals stay ours |
| 2 | Zero exit code on success, non-zero on failure | Adopted | One sentence and exit 1 |
| 3 | Send output to stdout | Adopted | Data on stdout, notices on stderr |
| 4 | Send messaging to stderr | Adopted | Same |
| 5 | Display extensive help text when asked | Adopted | Every command's `--help`, and the whole tree as the committed reference |
| 6 | Display concise help text by default | Adopted | One lowercase sentence per row in the command listing |
| 7 | Show full help on `-h` and `--help` | Adopted | Both, on every page of the tree |
| 8 | Provide a support path for feedback and issues | Rejected | Pre-release and self-hosted: there is no support channel to name, and the refusals carry the fix instead of pointing at one. Revisit when there is a channel |
| 9 | Link to the web version of the documentation in help | Adapted | Help names the command that prints the document (`Full descriptions: vinga-server config schema provider`), because the documents ship with the CLI and a URL would date |
| 10 | Lead with examples | Split | Adapted: examples lead the reference, generated from `vinga-server/examples/` rather than hand-written. Owed: examples on the command pages |
| 11 | Put loads of examples somewhere else | Adopted | The recipes region and `vinga-server/examples/` |
| 12 | Most common flags and commands at the start of the help | Adopted | The command listing order is the table's, restored after Typer's own ordering |
| 13 | Use formatting in your help text | Rejected | `rich_markup_mode=None` and `color=False`: the help pages are a committed artifact CI diffs byte for byte |
| 14 | If you can guess what they meant, suggest it | Rejected | A suggestion is built from the typed word. Closed deliberately in the #194 review rounds; see the refusal practice |
| 15 | A command expecting a pipe, run at a TTY, shows help and quits | Adapted | A secret set prompts rather than quitting, which is better; `-f -` quits with one sentence pointing at the help rather than printing a page, so every mistake in the grammar has one shape |
| 16 | Provide web-based documentation | Adopted | `docs/reference/cli.md` |
| 17 | Provide terminal-based documentation | Adopted | `--help`, plus `schema`, `reference`, `openapi`, `cli-reference` |
| 18 | Consider providing man pages | N/A | The deployment surface is a container image |
| 19 | Human-readable output is paramount | Adopted | One machine-readable shape (which is also the readable one) |
| 20 | Machine-readable output where it does not hurt usability | Adopted | Same |
| 21 | `--plain` when human output breaks machine output | Rejected | There is one rendering, with no color, no borders and no animation in it, so there is nothing for a plain mode to strip |
| 22 | Display formatted JSON if `--json` is passed | Deferred | The `--json` question, above |
| 23 | Display output on success, but keep it brief | Adopted | `wrote provider llm.claude` |
| 24 | If you change state, tell the user | Adopted | A write says what it did and when it takes effect |
| 25 | Make it easy to see the current state of the system | Adopted | `list`, `show`, `status`, `pending`, `prompt` |
| 26 | Suggest commands the user should run | Adopted | `NOTHING_CONFIGURED`, `NOTHING_PENDING`, the reload notice, the OTA guidance, and the `set-secret` lines an export writes |
| 27 | Actions crossing the boundary of the program's world should be explicit | Adopted | `reload` is a verb an operator runs; a write never reloads on its own |
| 28 | Increase information density with ASCII art | Rejected | Output determinism |
| 29 | Use color with intention | Rejected | Output determinism |
| 30 | Disable color when not in a terminal or when asked | N/A | Nothing colors |
| 31 | No animations when stdout is not an interactive terminal | Adopted | Stated in advance of the thing it governs: the owed progress line is stderr-only and terminal-only, which is the licence the determinism practice grants and its whole extent |
| 32 | Use symbols and emoji where they make things clearer | Rejected | Output determinism, and `printable` maps anything unprintable to `?` |
| 33 | Do not output information only the creators understand | Adopted | Refusals are operator sentences; the exception chain is suppressed on the way out |
| 34 | Do not treat stderr like a log file | Adopted | Stderr carries notices and one refusal sentence; the structured JSON log is the server's surface, not the CLI's |
| 35 | Use a pager for a lot of text | Rejected | The long outputs are documents meant to be redirected (`export > deployment.yaml`); a pager on a redirected stream is noise, and on a captured one is a hang |
| 36 | Catch errors and rewrite them for humans | Adopted | Every boundary raises `ConfigError` with a written sentence |
| 37 | Signal-to-noise ratio is crucial | Adopted | One sentence |
| 38 | Consider where the user will look first | Adopted | The sentence is the last thing on stderr |
| 39 | For unexpected errors, provide debug and traceback information | Rejected | Deliberately: an httpx exception carries the request URL and a Click context carries the argument list, so a traceback is where a token or a secret would surface. The no-leak posture outranks the debugging convenience. #289 was the case where one escaped anyway, found by this audit and since fixed; see the refusal practice |
| 40 | Make it effortless to submit bug reports | N/A | Pre-release; see 8 |
| 41 | Prefer flags to args | Adapted | Identity addressing |
| 42 | Have full-length versions of all flags | Adopted | `--config`, `--api-url`, `--file`, `--from-env` |
| 43 | Only use one-letter flags for commonly used flags | Adopted | `-f` is the only one |
| 44 | Multiple arguments are fine for simple actions against multiple things | Adopted | `bind-device <mac> <agent>...` takes a variable-length agent list |
| 45 | Two or more arguments for different things is probably wrong | Adopted | It is the rule that governs payload positionals: one group, last, homogeneous, and anything heterogeneous is a flag. The identity segments in front of it are capped separately, at three |
| 46 | Use standard names for flags where a standard exists | Adopted | `-f/--file`, `--force`, `--no-input`; `--json` is reserved rather than renamed |
| 47 | Make the default the right thing for most users | Adopted | The API address defaults to loopback on the port the file half names, which is the in-container case |
| 48 | Prompt for user input | Adopted | `set-secret` at a terminal |
| 49 | Never require a prompt | Adopted | `--from-env` and stdin |
| 50 | Confirm before doing anything dangerous | Adopted | Eight rows carry `destroys`, and each confirms at a terminal |
| 51 | Support `-` to read from stdin or write to stdout | Adopted | `-f -` |
| 52 | If a flag takes an optional value, allow a word like "none" | Adapted | `default_agent: null` in a document; the command form is a verb of its own (`clear-default-agent`) rather than a magic value |
| 53 | Make arguments, flags and subcommands order-independent where possible | Adopted | `--config` and `--api-url` are accepted before and after the command word, and a value given before it survives a command that was not given one |
| 54 | Do not read secrets directly from flags | Adopted | A credential is never an argument |
| 55 | Only prompt if stdin is an interactive terminal | Adopted | `isatty` decides between the no-echo prompt and a plain read |
| 56 | If `--no-input` is passed, do not prompt | Adopted | Every prompt in the grammar, with the destructive verb refusing rather than proceeding |
| 57 | Do not print a password as it is typed | Adopted | `getpass` |
| 58 | Let the user escape | Adopted | Nothing is trapped; Ctrl-C is the interpreter's |
| 59 | Be consistent across subcommands | Adopted | And mechanically: the rows are generated from the descriptor registry |
| 60 | Use consistent names for multiple levels of subcommand | Adopted | The noun word is spelled the same under every verb |
| 61 | Do not have ambiguous or similarly-named commands | Adopted | `bind-device` and `add-device` were two ways to bind one board told apart only by their help text; they are `device bind` and `device pending claim` since #223, on two sub-nouns, so what each addresses tells them apart |
| 62 | Validate user input | Adopted | The same pydantic models validate a write and the read of the answer |
| 63 | Responsive is more important than fast | Owed | The progress line |
| 64 | Show progress if something takes a long time | Owed | Same |
| 65 | Do stuff in parallel where you can | N/A | One command is one request |
| 66 | Make things time out | Adapted | Bound every wait that has a bound: one act has none, with the reason written down |
| 67 | Make it recoverable | Adopted | `apply` is one transaction refused whole; the documented recovery is reading the store back, and the rebuild path is a section of the reference |
| 68 | Make it crash-only | Adopted | The client holds no state between runs; every command is one request |
| 69 | People are going to misuse your program | Adopted | `printable`, the URL policy, the secret-never-an-argument sentence |
| 70 | Keep changes additive where you can | Adapted | Pre-release: nothing is owed to the current grammar, and what survives a re-cut does so on merit. The compatibility floor vinga does promise is the database's, not the CLI's |
| 71 | Warn before you make a non-additive change | Adapted | The changelog records grammar changes and the drift checks make them visible in review; there is no deprecation cycle before the first beta |
| 72 | Changing output for humans is usually OK | Adopted | With the generated-document drift checks as the mechanism that makes it visible |
| 73 | Do not have a catch-all subcommand | Adopted | An unrecognized first word is a fixed sentence naming the four groups |
| 74 | Do not allow arbitrary abbreviations of subcommands | Adopted | Click matches a command word exactly |
| 75 | Do not create a time bomb | Adopted | Nothing in the CLI expires. Activation codes expire on the server, and the empty listing says so |
| 76 | On Ctrl-C, exit as soon as possible | Adopted | Nothing is trapped |
| 77 | On Ctrl-C during clean-up, skip it | N/A | The CLI has no clean-up phase. The server does exactly this on its drain |
| 78 | Follow the XDG spec | Rejected | The deployment surface is a container, and the configuration file is named by `--config` or `VINGA_CONFIG` so the server and the CLI cannot disagree about which one it is. A home-directory default would be a second answer to that question |
| 79 | Ask consent before modifying configuration that is not yours | Adopted | Trivially: the CLI writes only through the API, into vinga's own store, and touches no file |
| 80 | Apply configuration parameters in order of precedence | Adopted | Where to reach, in a stated order |
| 81 | Environment variables are for behavior that varies with context | Adopted | `VINGA_API_URL`, `VINGA_CONFIG`, the token variable |
| 82 | Uppercase, numbers and underscores only | Adopted | |
| 83 | Aim for single-line values | Adopted | |
| 84 | Avoid commandeering widely used names | Adopted | Everything vinga defines is `VINGA_`-prefixed; the provider credential variables are named by the operator's own configuration |
| 85 | Check general-purpose environment variables where possible | N/A | None apply. `NO_COLOR` would, if anything colored |
| 86 | Read environment variables from `.env` where appropriate | Adopted | `load_dotenv(find_dotenv(usecwd=True))` at the entry point, with the real environment winning |
| 87 | Do not use `.env` as a substitute for a configuration file | Adopted | The file half is the configuration; `.env` carries variables only |
| 88 | Do not read secrets from environment variables | Rejected | The API token and `--from-env` are both environment reads, deliberately. On a container deployment the alternatives are a file on disk or an argument, and both are worse. The environment is how a credential is handed over once; the encrypted store is where it lives |
| 89 | Make it a simple, memorable word | Adopted | `vinga`, the console script #223 added |
| 90 | Use only lowercase letters, and dashes if you need them | Adopted | |
| 91 | Keep it short | Adopted | |
| 92 | Make it easy to type | Adopted | |
| 93 | Distribute as a single binary if possible | Adapted | The image ships the CLI, which is the intended path. A tool of its own is #223's; there is no published package yet, and the reference says so plainly rather than implying one |
| 94 | Make it easy to uninstall | Adopted | `uv tool uninstall`, or deleting the container |
| 95 | Do not phone home usage or crash data without consent | Adopted | And stronger: nothing phones home at all |
| 96 | Consider alternatives to collecting analytics | N/A | See 95 |

### Heroku CLI style guide

Thirty-five rules. The Node-specific dependency rules are grouped at
the end.

| # | Rule | Disposition | Where |
| --- | --- | --- | --- |
| 1 | The CLI is for humans before machines | Adopted | |
| 2 | Input and output consistent across commands, so users learn new ones | Adopted | The grammar is derived from the model it addresses |
| 3 | Topics are plural nouns; commands are verbs | Adapted | Singular where the noun addresses one entry (`provider set llm local`), plural where it is a collection (`sessions list`) |
| 4 | Plugins export a single topic | N/A | No plugin system, and none planned |
| 5 | Topic and command names are a single lowercase word without delimiters | Adapted | Kebab-case where more than one word is unavoidable, which rule 7 allows |
| 6 | Colons delineate subcommands (`heroku pg:credentials:repair-default`) | Rejected | Spaces. Heroku's technical reason is that a topic-level command taking an argument becomes ambiguous under spaces; vinga's one word that is both a group and a command (`show`) takes no positional argument, so the ambiguity does not arise, and a space-separated tree is what the generated reference walks |
| 7 | Kebab-case if multiple words are unavoidable | Adopted | `mcp-server`, `prompt-fragment`, `agent-defaults` |
| 8 | The root command of a topic lists those nouns | Adopted | `list` and `show`; under noun first, `sessions list` |
| 9 | Never create a `*:list` command | Adapted | `list` here is a whole-configuration summary tree, not a topic's listing. Where a noun's only verb is a listing (`sessions list`), the noun word alone prints help rather than data, which is what the rule's premise assumes away |
| 10 | Descriptions for all topics and commands | Adopted | Every row in `GROUPS` and `COMMANDS` |
| 11 | Descriptions fit 80-column screens | Adopted | `REFERENCE_WIDTH` |
| 12 | Descriptions begin with a lowercase character | Adopted | All forty-eight rows |
| 13 | Descriptions do not end in a period | Adopted | None ends in one and none carries one inside it, `apply` included since #223, and a test holds every row and group to it |
| 14 | Flags are preferred to arguments | Adapted | Identity addressing |
| 15 | Descriptions for all flags | Adopted | |
| 16 | Flag descriptions lowercase | Adopted | |
| 17 | Flag descriptions concise, for narrow screens | Adopted | |
| 18 | Flag descriptions do not end in a period | Adopted | |
| 19 | Arguments acceptable when there is one, or when they are obvious and in an obvious order | Adopted | The identity order is the URL's path order, printed identically in both places |
| 20 | Use inquirer for prompts | N/A | Node-specific |
| 21 | Prompting must never be required; args or flags bypass it | Adopted | Prompt where there is somebody to ask |
| 22 | Output commands print to stdout | Adopted | |
| 23 | Action commands show a spinner, on stderr, with a non-TTY fallback | Split | Adopted: the stderr half already holds for notices. Owed: the spinner, which is the progress line, and the non-terminal fallback is the rule it will be built to |
| 24 | Color is encouraged; standard colors per noun | Rejected | Output determinism |
| 25 | Color disabled by `--no-color`, `COLOR=false`, or a non-TTY | N/A | Nothing colors |
| 26 | Human-readable output should be grep-parseable; tables without borders | Adopted | The pending listing |
| 27 | `--json` when tables grow too long to fit | Deferred | The `--json` question |
| 28 | After general availability, do not change inputs and stdout in ways that break scripts | Adapted | Pre-release; see clig 70 |
| 29 | Offer `--json` and/or `--terse` where valuable | Deferred | Same |
| 30 | Stdout for all output | Adopted | |
| 31 | Stderr for warnings, errors and out-of-band information | Adopted | |
| 32 | No native dependencies | N/A | Node-specific. The equivalent holds by accident: the CLI is the server package, and the argument layer added exactly one dependency |
| 33 | Be judicious with dependencies | Adopted | In spirit: the argument layer added one direct dependency, and the offline commands pull in nothing further |
| 34 | Use dev dependencies for what is only needed to work on it | Adopted | The `dev` dependency group |
| 35 | Discouraged dependencies (request, underscore) | N/A | Node-specific |

### 12 Factor CLI Apps

Twelve factors.

| # | Factor | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Great help is essential: in-CLI and web, every spelling shows it, examples matter most | Split | Adopted and owed as clig 5 to 17 records it. One deliberate deviation: `vinga-server config` with nothing after it is a mistake in the grammar, not a request for help, so it answers with a sentence pointing at `--help` and exit 1, the way every other mistake does |
| 2 | Prefer flags to args; one type fine, two suspect, three never; support `--` | Adapted | Identity addressing; `--` is accepted, and the reference's rebuild section uses it |
| 3 | Make the version reachable several ways | Adopted | `--version` prints the installed distribution and its version; the running server answers `version` and `revision` on `/healthz` and in the OTA reply, and stamps both on every session record |
| 4 | Mind the streams: stdout is for output, stderr is for messaging | Adopted | Data on stdout, notices on stderr |
| 5 | Handle things going wrong: informative errors, a traceback or debug mode, error logs without ANSI | Split | Informative fixed sentences adopted; the traceback and debug-dump half rejected, per clig 39 |
| 6 | Be fancy: colors, spinners, OS notifications, with fallbacks and `NO_COLOR` respected | Split | Rejected: colors, ASCII art, emoji and OS notifications, under output determinism. Owed: the progress line, which the determinism practice licenses as an interactive affordance |
| 7 | Prompt if you can, never require | Adopted | |
| 8 | Use tables: one entry per row, no borders, plus `--columns`, `--no-truncate`, `--no-headers`, `--filter`, `--sort`, csv and json | Split | One entry per row and no borders adopted, and the pending listing is exactly that. The six table flags are rejected: they are a query language over an answer that is already small and already a document, and `grep`, `wc` and a YAML parser cover it |
| 9 | Be speedy | Adapted | What is pinned is import weight, by a test, and the offline commands open no database, need no key and reach no server. Nothing else is measured, and no startup budget is claimed |
| 10 | Encourage contributions | Adopted | MIT, public repository, upstream licence notices kept |
| 11 | Be clear about subcommands: multi-command, list them when given nothing, colons over spaces | Split | Multi-command adopted; spaces over colons (Heroku 6); listing on no arguments deliberately not done (factor 1) |
| 12 | Follow the XDG spec | Rejected | See clig 78 |
