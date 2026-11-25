# Dharmawiki Machine Translation — Setup, Editor Workflow & Deployment Guide

This guide covers everything needed for the Dharmawiki admin/engineering team to set up MediaWiki, onboard editors, verify the bot locally, and deploy the automated translation and publishing jobs on AWS.

---

## 1. Architecture Overview

The system translates Dharmawiki articles using the **BharatGen Param** (`param-translate`) model via an external Python batch bot (`dwtranslate`).

```
English article (Namespace 0)
   │  fetch via MediaWiki Action API (api.php)
   ▼
dwtranslate (mask templates, <ref>, categories, files, Sanskrit/IAST verses)
   │  call BharatGen Param API + SQLite translation memory
   ▼
Translation:<Title>/<lang>   (Namespace 3000 — unpublished, noindex, VisualEditor enabled)
   │  {{Machine translation|status=needs-review}}
   │  Editors review & edit in VisualEditor / source editor, then set status=reviewed
   ▼
dwtranslate publish
   │  moves page to <Title>/<lang> (history preserved), swaps banner to {{Translated page}},
   │  updates internal [[links]] to /<lang>, sets page language
   ▼
<Title>/<lang>               (Public translated article)
```

### Why this approach
- **No new MediaWiki translation extensions needed:** Works on Dharmawiki's existing **MediaWiki 1.35.4** using standard Action APIs (`query`, `edit`, `move`, `setpagelanguage`).
- **Editors keep their existing workflow:** Drafts live in a dedicated `Translation:` namespace with **VisualEditor** enabled, so editors use the exact same editor they already know.
- **No automatic publishing:** Translations are never published without an editor changing `status=needs-review` to `status=reviewed`.
- **Human edits are never overwritten:** Once a human edits a draft (or after a page is published), future source updates only add an `outdated=<revid>` flag with a `Special:Diff` link instead of replacing the text.
- **Works alongside UniversalLanguageSelector (ULS):** ULS continues to handle the site UI language and fonts; the bot sets each page's DB content language (`setpagelanguage`) so ULS renders translated scripts properly.

### Current Dharmawiki Site Baseline (checked against `dharmawiki.org/api.php`)
- **MediaWiki:** 1.35.4 (`Extension:Moderation`, `VisualEditor`, `UniversalLanguageSelector`, `ParserFunctions`, `Scribunto` installed)
- **Namespace 0 non-redirect articles:** 1,654 total (1 `Main Page` excluded + 187 in `Category:Hindi Articles` excluded = **1,466 candidate pages** to translate)

---

## 2. Part 1 — One-Time MediaWiki Server Setup (Admin, ~15 min)

### Step 1: Update `LocalSettings.php`
Append the contents of [`wiki/LocalSettings.translation.php`](wiki/LocalSettings.translation.php) to MediaWiki's `LocalSettings.php` on the Dharmawiki server (or `require_once` it).

What it configures:
- **Namespace `3000` (`Translation`) & `3001` (`Translation_talk`):** Stores unpublished machine-translated drafts with `noindex,nofollow` and excludes them from default site search.
- **VisualEditor on drafts:** `$wgVisualEditorAvailableNamespaces['Translation'] = true;`
- **Main-namespace subpages:** `$wgNamespacesWithSubpages[NS_MAIN] = true;` so published pages at `<Title>/hi` display a `< <Title>` back-link.
- **Per-page content language:** `$wgPageLanguageUseDB = true;`
- **Bot & moderation permissions:** Grants `pagelang` and `suppressredirect` to `bot`, and `skip-moderation` to `automoderated`.

### Step 2: Create the 3 Wiki Templates
Create these three pages on Dharmawiki by pasting the contents from `wiki/`:
1. `Template:Machine translation` ← [`wiki/Template_Machine_translation.wikitext`](wiki/Template_Machine_translation.wikitext)
   - Renders the draft review banner, tracks `status=needs-review` vs `status=reviewed`, and populates tracking categories (`Category:Machine translations needing review (<lang>)`, `Category:Translations ready to publish (<lang>)`, `Category:Outdated translations (<lang>)`).
2. `Template:Translated page` ← [`wiki/Template_Translated_page.wikitext`](wiki/Template_Translated_page.wikitext)
   - Renders the attribution header on published pages and populates `Category:Translated pages (<lang>)`.
3. `Template:Languages` ← [`wiki/Template_Languages.wikitext`](wiki/Template_Languages.wikitext)
   - Optional language-switcher bar (`Read in: English · हिन्दी · ...`) using `#ifexist` so links only appear once a translation is published.

### Step 3: Create the Bot Account & Bot Password
1. Create a wiki user named **`ParamTranslator`**.
2. Open **`Special:UserRights`** and add `ParamTranslator` to both groups:
   - **`bot`**
   - **`automoderated`** *(Required: `Extension:Moderation` is installed; without `automoderated`, every bot draft gets stuck in `Special:Moderation`.)*
3. Log in as `ParamTranslator` (or create a bot password for it) at **`Special:BotPasswords`**, e.g. password name `param`, with these grants:
   - High-volume editing
   - Edit existing pages
   - Create, edit, and move pages
   - Change page language
4. Your `WIKI_BOT_USER` value will be `ParamTranslator@param`.

### Step 4: Optional Language Switcher & Manual Pages
- Add `{{Languages}}` to articles (or a shared article header template) so readers can switch between published languages.
- `Main Page` is excluded from bot translation (`exclude_titles: ["Main Page"]`) because it uses widgets/layout—translate it manually if needed.
- Interface messages (`MediaWiki:Sidebar/hi`, etc.) are handled by `UniversalLanguageSelector` and can be translated manually on-wiki.

---

## 3. Part 2 — Editor Review & Publishing Workflow

1. **Where translated drafts are stored (hidden from public readers):**
   - Each machine-translated article is saved on Dharmawiki in the `Translation:` namespace at:
     `https://dharmawiki.org/index.php/Translation:<Title>/<lang>` (e.g., `Translation:Ayu_(आयुः)/hi`).
   - Because namespace `3000` is configured with `noindex,nofollow` and excluded from default search, regular visitors and search engines do not see unreviewed drafts.
2. **How editors find drafts waiting for review:**
   - Every draft includes `{{Machine translation|...|status=needs-review}}`, which automatically lists it in:
     `https://dharmawiki.org/index.php/Category:Machine_translations_needing_review_(<lang>)` (e.g., `Category:Machine translations needing review (hi)`).
   - Editors simply open or bookmark that category page to see all drafts awaiting review.
3. **What editors see & how they edit:**
   - Opening a draft displays a **red review banner** at the top (*"Machine translation – not yet published"*), showing a link to the English source page, the exact source revision (`Special:Permalink/<revid>`), and `Status: needs-review`, followed by the translated title and full translated article body.
   - Click **Edit** to review and fix the text using **VisualEditor** or the **source editor**—the exact same editor already used on Dharmawiki.
   - Check the banner's `title=...` parameter (the translated page heading) as well as the body text.
   - Sanskrit `<blockquote>` verses, `<ref>` citations, templates, and categories are preserved verbatim by the bot.
3. **Approve for publishing:** Edit the `{{Machine translation|...}}` banner at the top of the page and change:
   ```wikitext
   status=needs-review
   ```
   to:
   ```wikitext
   status=reviewed
   ```
   *(The bot also accepts `approved`, `ready`, or `publish`.)*
4. **Automated publishing:** On the next `dwtranslate publish --lang hi` run, the bot:
   - Moves `Translation:<Title>/hi` → `<Title>/hi` (preserving edit history, without leaving a redirect).
   - Swaps `{{Machine translation|...}}` for `{{Translated page|...}}`.
   - Re-links internal `[[Target|...]]` links to `[[Target/hi|...]]` for any target that now has a published `/hi` page.
   - Sets the page language to `hi` via `setpagelanguage`.
5. **Handling source updates:** If the English page is edited later:
   - **Bot-only drafts** (never edited by a human) are automatically re-translated.
   - **Human-edited drafts** and **published pages** are never overwritten; the bot adds `outdated=<new_revid>` to the banner, showing a **See what changed** (`Special:Diff`) link and listing the page in `Category:Outdated translations (hi)`.

---

## 4. Part 3 — Configuration & Local Verification

### Environment Variables
| Variable | Required | Description |
|---|---|---|
| `MANCH_API_KEY` | Yes | BharatGen Param API key (`https://llm-dev-mancha.bharatgen.dev/v1`) |
| `WIKI_BOT_USER` | For non-dry-run | Bot username from `Special:BotPasswords`, e.g. `ParamTranslator@param` |
| `WIKI_BOT_PASSWORD` | For non-dry-run | Bot password generated at `Special:BotPasswords` |
| `DWT_CONFIG` | Optional | Path to config YAML (defaults to `config.yaml`, or `/app/config.yaml` in Docker) |
| `DWT_STATE_PATH` | Optional | Local SQLite path (defaults to `state.sqlite`, or `/tmp/state.sqlite` in Docker) |
| `STATE_S3_URI` | Optional | S3 URI for persisting SQLite state between container runs, e.g. `s3://<bucket>/state.sqlite` |

### Local Setup & Recommended Rollout
```bash
cd dharmawiki-translator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit languages: if adding more than hi

export MANCH_API_KEY="..."
export WIKI_BOT_USER="ParamTranslator@param"
export WIKI_BOT_PASSWORD="..."

# 1. Verify API key + prompt
python -m dwtranslate try --lang hi "Ayu is the Samskrit term which refers to Life."

# 2. Dry-run a few pages locally (writes to ./output/hi/*.wikitext, does not touch the wiki)
python -m dwtranslate translate --lang hi --page "Ayu (आयुः)" --dry-run

# 3. Create real drafts for one category (10 pages)
python -m dwtranslate translate --lang hi --category "Category:Ayurveda" --limit 10

# (Editor step on wiki: open Category:Machine translations needing review (hi),
#  review/edit a Translation:<Title>/hi draft in VisualEditor or source editor,
#  and change status=needs-review to status=reviewed.)

# 4. Preview and publish reviewed drafts (moves draft -> <Title>/hi)
python -m dwtranslate publish --lang hi --dry-run
python -m dwtranslate publish --lang hi

# 5. Full run across all ~1,466 candidate articles (safe to re-run; uses SQLite translation memory)
python -m dwtranslate translate --lang hi --all

# Check status counts
python -m dwtranslate status
```

---

## 5. Part 4 — AWS Deployment (ECS Fargate + EventBridge + S3)

The Python bot **does not need to be installed on the Dharmawiki web server**. It communicates with `https://dharmawiki.org/api.php` and `https://llm-dev-mancha.bharatgen.dev/v1` over HTTPS, so it can run as a scheduled serverless batch container.

### Step 1: Build and Push the Docker Image
The repository includes [`Dockerfile`](Dockerfile) (`python:3.12-slim` with `config.example.yaml` baked in as `/app/config.yaml` and `DWT_STATE_PATH=/tmp/state.sqlite`):
```bash
docker build -t dwtranslate .
docker tag dwtranslate:latest <account>.dkr.ecr.<region>.amazonaws.com/dwtranslate:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/dwtranslate:latest
```

### Step 2: Create S3 State Bucket & IAM Permissions
- Create an S3 bucket (e.g., `dharmawiki-translator`) to store `state.sqlite`.
- When `STATE_S3_URI=s3://dharmawiki-translator/state.sqlite` is set, the bot automatically downloads `state.sqlite` at startup and uploads it on exit.
- Attach an IAM policy to the **ECS Task Role** allowing `s3:GetObject` and `s3:PutObject` on `arn:aws:s3:::dharmawiki-translator/state.sqlite`.

### Step 3: Store Secrets in AWS Secrets Manager
Store `MANCH_API_KEY`, `WIKI_BOT_USER`, and `WIKI_BOT_PASSWORD` in **AWS Secrets Manager** and inject them as `secrets` into the ECS Task Definition environment.

### Step 4: Create ECS Task Definition & EventBridge Schedules
- **Compute:** ECS Fargate (e.g., `0.5 vCPU`, `1 GB` RAM).
- **Network:** Outbound HTTPS (`443`) access to `dharmawiki.org` and `llm-dev-mancha.bharatgen.dev` (if Dharmawiki is in the same VPC, you can point `wiki.api_url` to its internal hostname).
- **Schedules (EventBridge Scheduler → ECS RunTask):**
  1. **Draft generation / refresh (nightly):**
     - Command override: `["translate", "--lang", "hi", "--all"]`
  2. **Publishing reviewed drafts (hourly or nightly, staggered so it does not overlap with `translate`):**
     - Command override: `["publish", "--lang", "hi"]`

> **Important:** Do not run `translate` and `publish` concurrently, as both read/write the same `state.sqlite` file in S3.

### Scale & Timing Expectations
- **First full run (Hindi):** ~1,466 candidate pages; at `concurrency: 4` and `edit_delay: 1.0`, expect the initial full pass to take several hours.
- **Subsequent runs:** Only new pages or changed paragraphs are sent to the Param API thanks to the SQLite translation memory (`tm` table).

---

## 6. Adding More Languages & Troubleshooting

### Adding Languages
1. Add the language under `languages:` in `config.yaml` (e.g., `mr: Marathi`, `ta: Tamil`, `te: Telugu`, `kn: Kannada`, `bn: Bengali`, `gu: Gujarati`).
2. Ensure `Template:Languages` on the wiki includes `{{#ifexist:{{BASEPAGENAME}}/<code>|...}}` for the new language.
3. Run a `--dry-run` on 5–10 pages first to verify `param-translate` output quality for that language before running `--all`.

### Common Issues
- **`queued-by-moderation` in logs/status:** The `ParamTranslator` user is missing the `automoderated` group (`Special:UserRights`) or `LocalSettings.translation.php` wasn't applied.
- **`setpagelanguage failed` warning:** Ensure `$wgPageLanguageUseDB = true;` is in `LocalSettings.php`, `ParamTranslator` is in the `bot` group, and the Bot Password has the **Change page language** grant.
- **High `fallback` counts in `status`:** Check if the Param model is dropping `<x>`/`<g>` tags for a specific language and tune `param.tag_prompt` in `config.yaml` if needed.
