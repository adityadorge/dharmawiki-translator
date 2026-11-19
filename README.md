# Dharmawiki × BharatGen Param — translation bot

Machine-translates Dharmawiki articles with the **Param** model (`param-translate`). Every translation is saved as an **unpublished draft inside the wiki**. Editors review and fix drafts in the **same editor they already use** (VisualEditor or source). They publish by changing one word.

## What the site is running (checked 2026-11-19)

Facts taken from `api.php?action=query&meta=siteinfo`:

| Item | Value | Why it matters |
|---|---|---|
| Platform | **MediaWiki 1.35.4** | The standard Action API is used for read, edit, move and setpagelanguage |
| Editors | VisualEditor plus the source editor | Drafts live inside the wiki, so no new editor is needed |
| Translate / ContentTranslation extensions | **not installed** | We use an external bot instead (see "Why this approach") |
| Extension:Moderation | **installed** | The bot must be in `automoderated`, or every draft gets stuck in Special:Moderation |
| ParserFunctions, Scribunto | installed | Used by the banner and language-switcher templates |
| Content | ~760 articles; Sanskrit verses in `<blockquote>`, IAST lines, `<ref>`, `{{NeedCitation}}` and other templates | The masker keeps all of these byte-for-byte |

## How it works

```
 English page ──fetch (API)──► mask markup ──► Param API ──► unmask/validate
      │                                                         │
      │                                     Translation:<Title>/hi  (draft, noindex)
      │                                     {{Machine translation|status=needs-review}}
      │                                                         │  editors fix text in the normal editor,
      │                                                         │  then set status=reviewed
      │                                       `publish` ──────► <Title>/hi  (public)
      │                                                          {{Translated page}}
      └─ source edited later ─► bot re-translates bot-only drafts; human-edited
                                drafts and published pages are only FLAGGED (outdated=…) with a diff link
```

**What gets translated.** Prose, headings, list items, table cells and the display text of links (`[[Ayurveda|…]]` keeps its target). A link is pointed at the `/hi` page once that page exists.

**What is never sent to the model.** Templates, `<ref>…</ref>`, `<references/>`, comments, categories, files, magic words, HTML tag markup, and Devanagari or IAST verse lines (detected by script).

**Safety checks.**
- Markup is replaced with XLIFF-style tags (`<x id="3"/>`, `<g id="5">text</g>`) before translation.
- After each model reply, the bot checks that every tag came back.
- If any tag is missing, it falls back to translating only the text between tags. That keeps the structure intact.
- Tests confirm that masking and unmasking with no translation in between returns the page byte-for-byte.

**Translation memory.** Every paragraph translation is cached in SQLite. When an article changes, only the changed paragraphs are sent to Param again.

**Never overwrites people.** A draft that a human has edited is never replaced. Published pages are never replaced either. When the source changes, the bot adds `outdated=<rev>` to the banner instead. That puts the page in `Category:Outdated translations (hi)` with a "see what changed" diff link.

## Why this approach (vs. alternatives)

| Option | Verdict |
|---|---|
| **External bot + draft namespace (this repo)** | Requires no new extension and no change to how editors work. Uses only APIs that ship with MediaWiki 1.35. Easy to add languages. |
| Extension:Translate + a custom Param MT backend | Every source page must be wrapped in `<translate>` tags. Editors would work in Special:Translate's segment editor (a different editor). It's heavy to upgrade on 1.35. Only worth it if you want sentence-level translation sync long-term. |
| Separate wiki per language (hi.dharmawiki.org) | Clean URLs, but you'd run a second wiki on AWS. You can still migrate to it later, because the bot only needs the `published` title pattern changed. |
| Using Moderation as the review gate | Editors can only approve or reject there, not comfortably edit first. A draft namespace is better for "machine translation, then human editing". |

## One-time wiki setup (an admin, ~15 minutes)

1. Append `wiki/LocalSettings.translation.php` to `LocalSettings.php` on the AWS server. It adds the `Translation:` namespace (id 3000, noindex, VisualEditor enabled), main-namespace subpages, and page-language support.
2. Create these pages and paste in the matching files from `wiki/`:
   - `Template:Machine translation`
   - `Template:Translated page`
   - `Template:Languages`
3. Create a user, e.g. **ParamTranslator**. Add it to the groups **bot** and **automoderated** (Special:UserRights).
4. Create a bot password at **Special:BotPasswords** (e.g. name `param`). Grant: high-volume editing, edit existing pages, create/edit/move pages, change page language. The login becomes `ParamTranslator@param`.
5. Optionally, add `{{Languages}}` to articles (or to a common template) so readers can switch languages.

## Run it

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
export MANCH_API_KEY=...  WIKI_BOT_USER='ParamTranslator@param'  WIKI_BOT_PASSWORD=...

python -m dwtranslate try --lang hi "Ayu is the Samskrit term which refers to Life."   # check key + prompt
python -m dwtranslate translate --lang hi --page "Ayu (आयुः)" --dry-run             # writes ./output/hi/*.wikitext only
python -m dwtranslate translate --lang hi --category "Category:Ayurveda" --limit 10   # real drafts in Translation:
python -m dwtranslate translate --lang hi --all                                       # everything (safe to re-run)
python -m dwtranslate publish   --lang hi --dry-run                                   # show what would go live
python -m dwtranslate publish   --lang hi
python -m dwtranslate status
```

**Suggested rollout.**
1. Do dry runs on 5–10 varied pages and have an editor judge the quality.
2. Tune `param.prompt` / `tag_prompt` if needed.
3. Do one category for real.
4. Then run `--all`.

**Adding a language** means one line under `languages:` (e.g. `mr: Marathi`), then `translate --lang mr --all`.

**Editor workflow.**
1. Open `Category:Machine translations needing review (hi)` and pick a page.
2. Edit it in the usual editor.
3. When done, change `status=needs-review` to `status=reviewed` in the banner.
4. The next `publish` run moves the page to `Title/hi` with its history kept, and swaps in the "Translated page" note.

## Deploy on AWS

The bot is a short batch job, so it doesn't need a server running all the time.

1. **Build and push the image.** `docker build -t dwtranslate . && docker push <account>.dkr.ecr.<region>.amazonaws.com/dwtranslate`
2. **Store secrets.** Put `MANCH_API_KEY`, `WIKI_BOT_USER` and `WIKI_BOT_PASSWORD` in **Secrets Manager**. Inject them into the ECS task definition as `secrets`.
3. **Persist state.** Create an S3 bucket and set the env var `STATE_S3_URI=s3://<bucket>/state.sqlite`. The task downloads the state at start and uploads it at the end. Give the task role `s3:GetObject`/`PutObject` on that key.
4. **Schedule it.** Create two **EventBridge Scheduler → ECS RunTask (Fargate)** schedules:
   - nightly: command `["translate","--lang","hi","--all"]`, which picks up new and changed pages
   - hourly or nightly: command `["publish","--lang","hi"]`
   - Don't run two tasks at once, because they share one SQLite state file.
5. **Network.** The task needs outbound HTTPS to `dharmawiki.org` and `llm-dev-mancha.bharatgen.dev`. If the wiki runs in the same VPC, you can point `wiki.api_url` at the internal hostname.

A first full run is roughly 760 pages × ~40 paragraphs ≈ 30k Param calls. At `concurrency: 4` that takes a few hours. Later runs only send changed paragraphs.

## Layout

```
dwtranslate/wikitext.py   masking, segmentation, verse detection, validation, unmasking
dwtranslate/llm.py        Param client: retries, translation-memory cache, long-paragraph chunking
dwtranslate/mediawiki.py  Action API client: bot-password login, edit, move, setpagelanguage
dwtranslate/pipeline.py   draft / refresh / outdated / publish logic
dwtranslate/store.py      SQLite state (+ S3 sync)
wiki/                     LocalSettings snippet + the three templates
tests/                    `pytest` — round-trip + full flow against a fake wiki
```

## Limitations to know about

- **`Main Page` is excluded.** It's widgets and layout, so it's best translated by hand once.
- **Navigation/sidebar.** The interface (`MediaWiki:Sidebar`, system messages) follows the user's language via UniversalLanguageSelector. Translate those messages by hand in `MediaWiki:Sidebar/hi` etc.
- **Untested against the real model.** The Param prompt format beyond `"Translate to Hindi: …"` hasn't been tried yet. If the model often drops `<x>`/`<g>` tags, the fallback kicks in and keeps structure, but sentences with links may read less smoothly. The `fallback` count in `status`/stats shows how often that happens.
