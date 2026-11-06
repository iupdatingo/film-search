"""
Film Search - a small Flask backend that proxies TheTVDB v4 API (https://thetvdb.com).

The API key is read from the TVDB_API_KEY environment variable (or a local .env
file), so it never appears in the HTML or JavaScript sent to the browser.

Run:
    py -m pip install -r requirements.txt
    py server.py
Then open http://localhost:5000
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()  # loads variables from .env into the environment (if the file exists)

TVDB_API_KEY = os.environ.get("TVDB_API_KEY", "").strip()
TVDB_PIN = os.environ.get("TVDB_PIN", "").strip()  # only needed for user-supported keys
TVDB_API_BASE = "https://api4.thetvdb.com/v4"      # API docs: https://thetvdb.github.io/v4-api/
TVDB_SITE = "https://www.thetvdb.com"              # public website (for "View on TheTVDB" links)
ARTWORK_BASE = "https://artworks.thetvdb.com"      # some endpoints return relative image paths

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

# TVDB login tokens are valid for one month; cache it so we log in only once.
_token = {"value": None, "expires": 0}


# --------------------------------------------------------------------------- TVDB API access

def tvdb_token(force=False):
    if not TVDB_API_KEY:
        raise RuntimeError("TVDB_API_KEY is not set. Add it to your .env file.")
    if _token["value"] and not force and time.time() < _token["expires"]:
        return _token["value"]

    body = {"apikey": TVDB_API_KEY}
    if TVDB_PIN:
        body["pin"] = TVDB_PIN
    try:
        resp = requests.post(f"{TVDB_API_BASE}/login", json=body, timeout=10)
    except requests.RequestException:
        raise RuntimeError("Could not reach TheTVDB. Check your internet connection.") from None
    if resp.status_code == 401:
        raise RuntimeError("TheTVDB rejected the API key. Check TVDB_API_KEY in your .env file.")
    if not resp.ok:
        raise RuntimeError(f"TheTVDB login error ({resp.status_code}). Please try again.")

    _token["value"] = resp.json()["data"]["token"]
    _token["expires"] = time.time() + 25 * 24 * 3600  # refresh a few days early
    return _token["value"]


def tvdb_get(path, **params):
    """GET an endpoint of TheTVDB v4 API, logging in (or re-logging in) as needed."""
    for attempt in (1, 2):
        headers = {"Accept": "application/json",
                   "Authorization": f"Bearer {tvdb_token(force=attempt == 2)}"}
        try:
            resp = requests.get(f"{TVDB_API_BASE}{path}", params=params, headers=headers, timeout=15)
        except requests.RequestException:
            raise RuntimeError("Could not reach TheTVDB. Check your internet connection.") from None
        if resp.status_code == 401 and attempt == 1:
            continue  # token expired - log in again and retry once
        break
    # Never pass requests' own error text to the browser: it includes the URL and headers.
    if resp.status_code == 401:
        raise RuntimeError("TheTVDB rejected the API key. Check TVDB_API_KEY in your .env file.")
    if resp.status_code == 404:
        raise RuntimeError("Film not found.")
    if not resp.ok:
        raise RuntimeError(f"TheTVDB error ({resp.status_code}). Please try again.")
    return resp.json()


# --------------------------------------------------------------------------- helpers

def full_image(path):
    if not path or "/images/missing/" in path:  # TVDB's grey "no image" placeholder
        return None
    return path if path.startswith("http") else f"{ARTWORK_BASE}{path}"


def money(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n >= 1e9:
        return f"${n / 1e9:,.2f} billion"
    if n >= 1e6:
        return f"${n / 1e6:,.0f} million"
    return f"${n:,.0f}" if n > 0 else None


def youtube_key(url):
    m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def pick_trailer(trailers, language="eng"):
    """Choose a YouTube trailer, preferring one in the film's language, then English."""
    yt = [t for t in trailers if youtube_key(t.get("url"))]
    for test in (lambda t: t.get("language") == language,
                 lambda t: t.get("language") == "eng"):
        match = next((t for t in yt if test(t)), None)
        if match:
            return youtube_key(match["url"])
    return youtube_key(yt[0]["url"]) if yt else None


def translation(items, field, lang="eng"):
    return next((t.get(field) for t in items or [] if t.get("language") == lang and t.get(field)), None)


def unique(seq):
    return list(dict.fromkeys(x for x in seq if x))


def cast_list(people):
    """Actors in billing order; an actor playing several roles appears once (roles joined)."""
    cast = {}
    for c in people:
        if c.get("peopleType") != "Actor" or not c.get("personName"):
            continue
        key = c.get("peopleId") or c["personName"]
        role = (c.get("name") or "").strip()
        if key in cast:
            if role and role not in cast[key]["role"]:
                cast[key]["role"] = " / ".join(filter(None, [cast[key]["role"], role]))
        else:
            cast[key] = {"name": c["personName"], "role": role,
                         "photo": full_image(c.get("personImgURL") or c.get("image"))}
    return list(cast.values())


def search_item(m):
    """Shape one /search result for the browser."""
    return {
        "id": m.get("tvdb_id"),
        "title": (m.get("translations") or {}).get("eng") or m.get("name"),
        "year": m.get("year") or "",
        "overview": (m.get("overviews") or {}).get("eng") or m.get("overview"),
        "poster": full_image(m.get("image_url") or m.get("thumbnail")),
        "director": m.get("director"),
    }


def rank_by_query(items, query):
    """Titles that start with / contain the query come first; otherwise keep TVDB's order."""
    q = query.lower()
    def score(m):
        t = (m["title"] or "").lower()
        return 0 if t == q else 1 if t.startswith(q) else 2 if q in t else 3
    return sorted(items, key=score)


# --------------------------------------------------------------------------- routes

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "film-search.html")


@app.route("/api/suggest")
def suggest():
    """Type-ahead suggestions while the user types in the search box."""
    query = request.args.get("query", "").strip()
    if len(query) < 2:
        return jsonify({"results": []})
    try:
        data = tvdb_get("/search", query=query, type="movie", limit=15)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    items = rank_by_query([search_item(m) for m in data.get("data") or [] if m.get("tvdb_id")], query)
    return jsonify({"results": [{k: m[k] for k in ("id", "title", "year", "poster", "director")}
                                for m in items[:8]]})


@app.route("/api/search")
def search():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "Please enter a film title."}), 400
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    try:
        data = tvdb_get("/search", query=query, type="movie", page=page - 1)  # TVDB pages start at 0
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    results = [search_item(m) for m in data.get("data") or [] if m.get("tvdb_id")]
    if page == 1:
        results = rank_by_query(results, query)
    links = data.get("links") or {}
    return jsonify({"results": results, "total": links.get("total_items", len(results)),
                    "page": page, "has_more": bool(links.get("next"))})


@app.route("/api/movie/<int:movie_id>")
def movie(movie_id):
    try:
        m = tvdb_get(f"/movies/{movie_id}/extended", meta="translations", short="false")["data"]
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    translations = m.get("translations") or {}
    names = translations.get("nameTranslations") or []
    overviews = translations.get("overviewTranslations") or []
    lang = m.get("originalLanguage") or "eng"

    title = translation(names, "name") or m.get("name", "")
    year = m.get("year") or ""
    people = sorted(m.get("characters") or [], key=lambda c: c.get("sort") or 999)
    remote = {r.get("sourceName"): r.get("id") for r in m.get("remoteIds") or []}
    imdb_id = remote.get("IMDB")
    homepage = remote.get("Official Website")
    backdrop = next((a["image"] for a in m.get("artworks") or [] if a.get("type") == 15), None)

    ratings = m.get("contentRatings") or []
    certificate = (next((r for r in ratings if r.get("country") == "usa"), None)
                   or next((r for r in ratings if r.get("country") == m.get("originalCountry")), None)
                   or (ratings[0] if ratings else None))
    awards = m.get("awards") or []
    wins = [a for a in awards if a.get("isWinner")]

    return jsonify({
        "id": m["id"],
        "title": title,
        "tagline": translation(names, "tagline") or translation(overviews, "tagline"),
        "overview": translation(overviews, "overview") or translation(overviews, "overview", lang),
        "year": year,
        "release_date": (m.get("first_release") or {}).get("date"),
        "runtime": m.get("runtime"),
        "status": (m.get("status") or {}).get("name"),
        "certificate": certificate.get("name") if certificate else None,
        "language": m.get("originalLanguage"),
        "country": m.get("originalCountry"),
        "genres": [g["name"] for g in m.get("genres") or []],
        "studios": unique(st.get("name") for st in m.get("studios") or []),
        "budget": money(m.get("budget")),
        "box_office": money(m.get("boxOffice")),
        "awards": {
            "wins": len(wins),
            "nominations": len(awards),
            "oscars": sum(1 for a in wins if a.get("name") == "Academy Awards"),
        },
        "directors": unique(c.get("personName") for c in people if c.get("peopleType") == "Director"),
        "writers": unique(c.get("personName") for c in people if c.get("peopleType") == "Writer")[:4],
        "cast": cast_list(people)[:12],
        "poster": full_image(m.get("image")),
        "backdrop": backdrop,
        "trailer_key": pick_trailer(m.get("trailers") or [], lang),
        "links": {
            "tvdb": f"{TVDB_SITE}/movies/{m['slug']}" if m.get("slug") else None,
            "imdb": f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None,
            "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search="
                         + requests.utils.quote(f"{title} {year} film"),
            "homepage": homepage if homepage and homepage.startswith("http") else None,
            "youtube_search": "https://www.youtube.com/results?search_query="
                              + requests.utils.quote(f"{title} {year} trailer"),
        },
    })


_filter_cache = {}
AUDIENCE_GENRES = {11, 16, 17, 27, 13}  # Family, Children, Animation, Anime, Documentary


def films_in_genre(genre_id, country, lang):
    """Most popular films in a genre (TheTVDB /movies/filter), cached while the server runs."""
    key = (genre_id, country, lang)
    if key not in _filter_cache:
        data = tvdb_get("/movies/filter", genre=genre_id, sort="score", sortType="desc",
                        country=country, lang=lang)
        _filter_cache[key] = [
            {"id": str(f["id"]), "title": f.get("name"), "year": f.get("year") or "",
             "poster": full_image(f.get("image"))}
            for f in (data.get("data") or [])[:150]
        ]
    return _filter_cache[key]


@app.route("/api/movie/<int:movie_id>/similar")
def similar(movie_id):
    """Suggestions for a film: its collection, more from its director, and films sharing its genres."""
    try:
        m = tvdb_get(f"/movies/{movie_id}/extended", short="false")["data"]
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    this_id = str(movie_id)
    people = m.get("characters") or []
    d = next((c for c in people if c.get("peopleType") == "Director" and c.get("peopleId")), None)
    official = next((l for l in m.get("lists") or [] if l.get("isOfficial")), None)
    country, lang = m.get("originalCountry") or "usa", m.get("originalLanguage") or "eng"
    genre_ids = [g["id"] for g in m.get("genres") or []]

    def director_films():
        if not d:
            return []
        person = tvdb_get(f"/people/{d['peopleId']}/extended")["data"]
        films, seen = [], {this_id}
        for c in person.get("characters") or []:
            mid, film = str(c.get("movieId") or ""), c.get("movie") or {}
            if mid and mid not in seen and c.get("peopleType") == "Director":
                seen.add(mid)
                films.append({"id": mid, "title": film.get("name"), "year": film.get("year") or "",
                              "poster": full_image(film.get("image"))})
        return sorted(films, key=lambda f: f["year"] or "0", reverse=True)

    def collection_films():
        """The film's official collection (e.g. a trilogy), from TheTVDB's official lists."""
        if not official:
            return None, []
        lst = tvdb_get(f"/lists/{official['id']}/extended")["data"]
        ids = [str(e["movieId"]) for e in sorted(lst.get("entities") or [], key=lambda e: e.get("order") or 0)
               if e.get("movieId") and str(e["movieId"]) != this_id][:12]
        films = [tvdb_get(f"/movies/{mid}")["data"] for mid in ids]
        return lst.get("name"), [{"id": str(f["id"]), "title": f.get("name"), "year": f.get("year") or "",
                                  "poster": full_image(f.get("image"))} for f in films]

    def pool(gid):
        return films_in_genre(gid, country, lang) or films_in_genre(gid, "usa", "eng")

    def safe(future, default):
        try:
            return future.result()
        except Exception:
            return default  # suggestions are optional - never fail the whole request

    # Fetch everything from TheTVDB at the same time instead of one after another
    excluded_genres = AUDIENCE_GENRES - set(genre_ids)
    with ThreadPoolExecutor(max_workers=10) as ex:
        f_director = ex.submit(director_films)
        f_collection = ex.submit(collection_films)
        f_pools = {gid: ex.submit(pool, gid) for gid in genre_ids[:4]}
        f_excluded = [ex.submit(pool, gid) for gid in excluded_genres]
    by_director = safe(f_director, [])
    collection, in_collection = safe(f_collection, (None, []))

    # Rank popular films by how many genres they share with this one
    counts, films = {}, {}
    for gid in genre_ids[:4]:
        for rank, f in enumerate(safe(f_pools[gid], [])):
            films[f["id"]] = f
            shared, best = counts.get(f["id"], (0, rank))
            counts[f["id"]] = (shared + 1, min(best, rank))

    # Leave out kids/animation/documentary titles unless this film is one too
    exclude = {this_id} | {f["id"] for f in by_director} | {f["id"] for f in in_collection}
    for fut in f_excluded:
        exclude |= {f["id"] for f in safe(fut, [])}

    candidates = [fid for fid in counts if fid not in exclude]
    need = min(2, len(genre_ids[:4]))  # prefer films sharing at least two genres
    strong = [fid for fid in candidates if counts[fid][0] >= need]
    if len(strong) >= 6:
        candidates = strong
    ranked = sorted(candidates, key=lambda fid: (-counts[fid][0], counts[fid][1]))

    return jsonify({"collection": collection if in_collection else None, "in_collection": in_collection,
                    "director": d.get("personName") if d else None, "by_director": by_director[:12],
                    "similar": [films[fid] for fid in ranked[:12]]})


if __name__ == "__main__":
    if not TVDB_API_KEY:
        print("WARNING: TVDB_API_KEY is not set - add it to your .env file.")
    app.run(debug=True, port=5000)
