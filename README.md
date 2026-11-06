# Film Search App

A simple Flask web app that searches for films using [TheTVDB](https://www.thetvdb.com/) v4 API.

## Features
- Search films by title, with suggestions that appear as you type (use the arrow keys and Enter, or click).
- Results show the poster, year and director, with a "Load more" button for long result lists.
- Film details: trailer, age certificate, release date, runtime, language, genres, overview,
  director, writers, studio, budget, box office, awards and cast (with photos and roles).
- Suggestions for each film: other films in its collection (e.g. a trilogy), "You might also like"
  (popular films with the same genres), and "More from" the director. Click any of them to open it.
- Links to TheTVDB, IMDb, Wikipedia, YouTube and the official site.

## Project Structure
```
├── .env.example       # Template - copy to .env and add your API key
├── .env               # Your real API key (private, never committed)
├── .gitignore         # Keeps .env and .venv out of git
├── server.py          # Flask backend - talks to TheTVDB API
├── film-search.html   # Web page (HTML, CSS and JavaScript)
└── requirements.txt   # Python dependencies
```

The browser only talks to `server.py`; the server adds your API key when it calls TheTVDB,
so the key is never sent to the web page.

---

## Prerequisites
- Python 3.8 or higher - download from [python.org](https://www.python.org/downloads/)
- A TheTVDB API key - see [TheTVDB API information](https://www.thetvdb.com/api-information)

## Run on Your Local Host (Windows)

Open PowerShell or Command Prompt in the project folder (`prj-film-search`).

### 1. Create and activate a virtual environment
```
py -m venv .venv
```
PowerShell:
```
.venv\Scripts\Activate.ps1
```
Command Prompt:
```
.venv\Scripts\activate.bat
```
If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

### 2. Install the dependencies
```
pip install -r requirements.txt
```

### 3. Add your API key
The app reads the key from the `TVDB_API_KEY` environment variable. Copy the example file to `.env`:
```
copy .env.example .env
```
Then open `.env` and put your key in it:
```
TVDB_API_KEY=your_api_key_here
```
If your key is a *user-supported* key, also add your subscriber PIN:
```
TVDB_PIN=your_pin_here
```
`.env` is listed in `.gitignore`, so it stays on your machine. You can manage your keys on your
[TheTVDB API key dashboard](https://www.thetvdb.com/dashboard/account/apikey).

### 4. Start the server
```
python server.py
```

### 5. Open the app
Go to **http://localhost:5000** (or http://127.0.0.1:5000) in your web browser, type a film title
and press Search. Click a film to see its details and trailer.

Press `Ctrl+C` in the terminal to stop the server.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'flask'`**
The virtual environment isn't active or the dependencies aren't installed. Activate `.venv`
(step 1) and run `pip install -r requirements.txt` again.

**"TVDB_API_KEY is not set"**
Make sure `.env` is in the same folder as `server.py` and the line is `TVDB_API_KEY=...`
with no spaces or quotes around the key.

**"TheTVDB rejected the API key"**
Check the key on your [API key dashboard](https://www.thetvdb.com/dashboard/account/apikey).
User-supported keys also need `TVDB_PIN` in `.env`.

**Port 5000 is already in use**
Change the port on the last line of `server.py`, e.g. `app.run(debug=True, port=5001)`,
then open http://localhost:5001.

## API Reference
- TheTVDB v4 API docs: https://thetvdb.github.io/v4-api/
- API base URL used by the app: `https://api4.thetvdb.com/v4`
