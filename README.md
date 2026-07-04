# Four-School Fiqh Research Bot

A Telegram research bot for fiqh research within the four Sunni legal schools.

The fiqh commands search the **Turath** database only, using these configurable categories:

- Hanbali: category `17`
- Hanafi: category `14`
- Maliki: category `15`
- Shafi‘i: category `16`

It also supports a separate **Usul al-Fiqh** research category: `11`. This is separate from the four schools and is not included in `/all`.

The bot uses OpenAI only to create Arabic search queries and synthesize an answer from the excerpts it retrieved. It does not use vector stores or uploaded-file retrieval.

An optional Brave Search integration adds fatwa-site commands for IslamQA and IslamWeb. Those commands remain disabled unless you set `BRAVE_SEARCH_API_KEY` in `.env`.

## Features

- Search one school with `/hanbali`, `/hanafi`, `/maliki`, or `/shafi`.
- Search all four schools with `/all`.
- Search selected schools together, for example `/shafi /maliki ...`.
- Search Usul al-Fiqh category 11 with `/usool`.
- Search configured preferred Usul al-Fiqh texts only with `/usoolpref`.
- Search one exact Turath book with `/book BOOK_ID [question]`.
- Set preferred books for each school in `.env`.
- Normal school commands return preferred-book results first and then results from other books in that school category.
- Search preferred books only with `/hanbalipref`, `/hanafipref`, `/malikipref`, `/shafipref`, and `/allpref`.
- Control retrieval breadth, nearby-page context, prompt length, retries, and pacing from `.env`.
- Produce plain-text answers with clean paragraphs and a verified `Sources used` section.
- Replies strictly in the language of the user’s question. English questions remain in English even when they include Arabic fiqh terms; Arabic questions remain in Arabic.
- Optionally search fatwas from IslamQA and IslamWeb with `/fatwa`, `/islamqa`, and `/islamweb`.

## Create a Telegram bot and get its token

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Enter the display name for the bot.
4. Enter a unique username ending in `bot`, for example `four_school_fiqh_bot`.
5. BotFather will send you an HTTP API token.
6. Copy the token to your `.env` file as `TELEGRAM_BOT_TOKEN`.

Keep this token private. Anyone with the token can control the bot. If it is ever exposed, revoke it through BotFather and replace it in `.env`.

Get your numeric Telegram user ID by messaging **@userinfobot**. Add that number to `ALLOWED_TELEGRAM_USER_ID`.

If you would like the bot to respond to you alone and nobody else on the internet, then set the `PUBLIC_BOT` in your `.env` file to false, otherwise, set it to true.

## Find Turath book IDs

Preferred-book settings and the `/book` command use numeric Turath book IDs.

1. Go to `https://app.turath.io/`.
2. Click **كتب**.
3. Find the book you want and open it.
4. Look at the URL. The number immediately after `/book/` is the book ID.

For example:

```text
https://app.turath.io/book/21731
```

The book ID is `21731`.

Use it in a command like this:

```text
/book 21731 حكم صيام المسافر
```

Or add it to a preferred-books setting in `.env`:

```dotenv
HANBALI_PREFERRED_BOOKS=21731,1262,1679
```

## Installation

### 1. Create the project folder

Clone the repository or download the files, then open a terminal in the project folder.

```bash
cd fiqh-four-schools-turath-bot
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create `.env`

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

Open `.env` and set:

```dotenv
TELEGRAM_BOT_TOKEN=your_telegram_token
OPENAI_API_KEY=your_openai_key
ALLOWED_TELEGRAM_USER_ID=your_numeric_telegram_id
```

Keep `.env` private. The provided `.gitignore` prevents it from being committed.

### 5. Run the bot

```bash
python fiqh_research_bot.py
```

You should see:

```text
Four-School Fiqh Research Bot is running...
```

## Configure preferred books

Each school has a comma-separated setting:

```dotenv
HANBALI_PREFERRED_BOOKS=1262,1679,19228
HANAFI_PREFERRED_BOOKS=
MALIKI_PREFERRED_BOOKS=
SHAFI_PREFERRED_BOOKS=
USOOL_PREFERRED_BOOKS=
```

The included `.env.example` already fills `HANBALI_PREFERRED_BOOKS` with the existing Hanbali list:

```dotenv
HANBALI_PREFERRED_BOOKS=1262,1679,19228,21693,30004,622,6910,12052,147311,20878,19233,21731,1153,151,21743,7360,20589,14386,16772,1241,18353,96876,30066,6115,7725,17817,12216
```

### How normal school commands work

A normal category command, such as `/hanbali` or `/usool`, always performs both searches:

1. It searches the configured preferred books first.
2. It searches the full configured school category.
3. It removes preferred-book IDs from the category result group.
4. It presents preferred-book material first, then material from books outside the preferred list.

The answer is instructed to distinguish the groups, for example:

`According to the preferred books configured for the Hanbali category...`

and separately:

`According to retrieved books outside that preferred list in the Hanbali category...`

A preferred-only command, such as `/hanbalipref` or `/usoolpref`, searches only its corresponding `*_PREFERRED_BOOKS` setting. It never searches the full category. When no IDs are configured, the bot explains which `.env` value must be filled.

## Command guide

### `/start`

Shows the in-bot command menu.

### `/help`

Shows the in-bot command menu again.

### `/hanbali [question]`

Searches Hanbali preferred books first, then other Hanbali books from the configured category.

```text
/hanbali حكم الجمع في السفر
```

### `/hanafi [question]`

Searches Hanafi preferred books first, then other Hanafi books from the configured category.

```text
/hanafi Does touching a spouse break wudu?
```

### `/maliki [question]`

Searches Maliki preferred books first, then other Maliki books from the configured category.

```text
/maliki What are the conditions of zakat on trade goods?
```

### `/shafi [question]`

Searches Shafi‘i preferred books first, then other Shafi‘i books from the configured category.

```text
/shafi ما شروط صلاة الجماعة؟
```

### `/usool [question]`

Searches Turath category `11` for Usul al-Fiqh. It searches `USOOL_PREFERRED_BOOKS` first, then separately searches books outside that preferred list in category 11. `/all` does not include this category.

```text
/usool What is the difference between العام and الخاص?
/usool ما الفرق بين العام والخاص؟
```

### `/usoolpref [question]`

Searches only the book IDs listed in `USOOL_PREFERRED_BOOKS`. When the setting is empty, the bot tells the user to add IDs to `.env`.

```text
/usoolpref حكم الأمر بعد الحظر
```

### `/all [question]`

Searches all four fiqh-school categories: Hanbali, Hanafi, Maliki, and Shafi‘i. It does not include Usul al-Fiqh category 11; use `/usool` for that.

```text
/all What is the ruling on fasting while traveling?
```

### Selected-school searches

Place multiple ordinary school commands at the beginning of one message. The bot searches only the schools you list.

```text
/shafi /maliki What invalidates wudu?
/hanafi /hanbali حكم الجمع في السفر
```

Do not mix ordinary school commands with preferred-only commands in the same request.

### `/book BOOK_ID [question]`

Searches only the one Turath book ID you specify. It does not search the larger school category, other preferred books, or external websites.

```text
/book 21731 What is the ruling on fasting while traveling?
/book 21731 حكم صيام المسافر
```

The first value must be a positive numeric book ID.

### Preferred-books-only commands

These search only the preferred book IDs listed in `.env`:

```text
/hanbalipref شروط صلاة الجماعة
/hanafipref What invalidates wudu?
/malikipref حكم بيع الغرر
/shafipref شروط الزكاة
/usoolpref حكم الأمر بعد الحظر
/allpref حكم البيع بالتقسيط
```

Underscore aliases also work:

```text
/hanbali_pref [question]
/hanafi_pref [question]
/maliki_pref [question]
/shafi_pref [question]
/usool_pref [question]
/all_pref [question]
```

### Plain-text question

A message without a command uses the same behavior as `/all`.

```text
What is the ruling on fasting while traveling?
```

## Optional Brave Search and fatwa commands

The fiqh commands do not need Brave. Brave is required only for the website-search commands below:

```text
/fatwa [question]
/islamqa [question]
/islamweb [question]
```

### Set up Brave Search

1. Obtain a Brave Search API key from [Brave Search API](https://brave.com/search/api/).
2. Add it to your `.env` file:

```dotenv
BRAVE_SEARCH_API_KEY=your_brave_search_api_key
```

3. Restart the bot after saving `.env`.

If `BRAVE_SEARCH_API_KEY` is missing, the bot does not make a website request. It tells the user to obtain a Brave API key and enter it in `.env`.

### `/fatwa [question]`

Searches both IslamQA and IslamWeb through Brave Search, retrieves matching fatwa pages, and answers only from the retrieved excerpts.

```text
/fatwa What is the ruling on combining prayers while traveling?
```

### `/islamqa [question]`

Searches IslamQA only.

```text
/islamqa حكم جمع الصلاة في السفر
```

### `/islamweb [question]`

Searches IslamWeb only.

```text
/islamweb حكم جمع الصلاة في السفر
```

## Retrieval and context controls

These `.env` values can be adjusted without editing Python:

| Variable | Default | What it controls |
|---|---:|---|
| `TURATH_SEARCH_QUERY_COUNT` | `4` | Arabic search phrases generated for each question |
| `TURATH_RESULTS_PER_QUERY` | `5` | Preferred-book results requested for each query |
| `TURATH_GENERIC_RESULTS_PER_QUERY` | `15` | Wider category candidate pool before preferred IDs are removed |
| `TURATH_MAX_RESULTS_PER_SCHOOL` | `10` | Maximum retained results per school and retrieval group |
| `TURATH_CONTEXT_TOP_N` | `5` | Number of results expanded with nearby pages; `0` means all |
| `TURATH_CONTEXT_RADIUS` | `3` | Pages fetched before and after an expanded hit; `0` disables expansion |
| `TURATH_RESULT_TEXT_CHARS` | `2600` | Characters from each matching result supplied to OpenAI |
| `TURATH_CONTEXT_PAGE_CHARS` | `3500` | Characters retained from each nearby context page |
| `TURATH_REQUEST_TIMEOUT` | `25` | Seconds allowed for one Turath request |
| `TURATH_MAX_RETRIES` | `3` | Attempts for a temporary Turath failure |
| `TURATH_BACKOFF_SECONDS` | `2` | Base pause before a retry |
| `TURATH_DELAY_BETWEEN_CALLS_MS` | `350` | Pause between Turath calls |
| `BRAVE_SEARCH_RESULTS_PER_QUERY` | `5` | Brave results checked per fatwa search query |
| `FATWA_MAX_PAGES_PER_SITE` | `4` | Fatwa pages fetched from each selected website |

Higher values may provide more evidence, but they also increase response time, Turath/API traffic, and OpenAI input use.

## Important research limitation

This is a retrieval and synthesis tool, not a substitute for a qualified scholar’s personal fatwa. The bot is instructed to describe only what its retrieved excerpts state.

## Project structure

```text
fiqh-four-schools-turath-bot/
├── fiqh_research_bot.py
├── .env.example
├── requirements.txt
├── .gitignore
└── README.md
```
