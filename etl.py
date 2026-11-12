"""
etl.py - movie data pipeline

Reads the MovieLens CSV files, cleans them, loads them into PostgreSQL,
and then adds extra movie details from the OMDb API.

How to run:    python etl.py
Useful flags:  python etl.py --skip-api        only load the CSV files, no API calls
               python etl.py --max-calls 100   use at most 100 API calls in this run

The steps follow the order in the assignment:
    1. Extract    - read movies.csv and ratings.csv
    2. Transform  - clean the data and create the new columns (like decade)
    3. Load       - put the clean data into PostgreSQL
    4. Enrich     - ask the OMDb API for more details about the movies,
                    clean that data too, and load it
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()  # reads DATABASE_URL and OMDB_API_KEY from the .env file

log = logging.getLogger("etl")

BASE_DIR = Path(__file__).resolve().parent

# ---- settings ---------------------------------------------------------------
OMDB_URL = os.getenv("OMDB_URL", "https://www.omdbapi.com/")

# The free key gives 1000 calls per day. I stop at 900 so I still have some
# calls left over for retries or for trying things out by hand.
DEFAULT_API_BUDGET = 900

MAX_RETRIES_PER_CALL = 3     # if a call fails because of the network, try it up to 3 times
MAX_ERROR_ATTEMPTS = 3       # if a movie keeps failing over several runs, stop trying after 3
BACKOFF_SECONDS = 1.5        # wait a bit before retrying (1.5s, then 3s)
REQUEST_TIMEOUT = 10         # give up on a call after 10 seconds


# =============================================================================
# 1. EXTRACT - read the CSV files
# =============================================================================

def extract_csvs(data_dir: Path):
    movies_path = data_dir / "movies.csv"
    ratings_path = data_dir / "ratings.csv"

    for path in (movies_path, ratings_path):
        if not path.exists():
            sys.exit(f"Could not find {path}. Copy movies.csv and ratings.csv into the data/ folder.")

    # I read every column as plain text first. If pandas guessed the types, one bad
    # value could change a whole column in a way I wouldn't notice. This way I
    # convert the columns myself later, and bad values turn into NaN so I can count them.
    movies = pd.read_csv(movies_path, dtype=str, encoding="utf-8")
    ratings = pd.read_csv(ratings_path, dtype=str, encoding="utf-8")

    log.info("Read %d rows from movies.csv and %d rows from ratings.csv", len(movies), len(ratings))
    return movies, ratings


# =============================================================================
# 2. TRANSFORM - clean the CSV data
# =============================================================================

# Titles in MovieLens look like this:
#   "American President, The (1995)"   -> the article "The" is moved to the end
#   "City of Lost Children, The (Cite des enfants perdus, La) (1995)"   -> another title in brackets
#   "Babylon 5"                        -> no year at all
# These patterns help me clean them so the OMDb search can find the movie.
_YEAR_AT_END = re.compile(r"\s*\((\d{4})\)\s*$")
_PAREN_AT_END = re.compile(r"\s*\([^()]*\)\s*$")
_ARTICLE_AT_END = re.compile(r",\s+(The|A|An|Les|La|Le|Il|Das|Der|Die|El|Los|Las|Un|Une)$", re.IGNORECASE)
_LAPOSTROPHE_AT_END = re.compile(r",\s+(L')$", re.IGNORECASE)


def split_title(raw_title: str):
    """'American President, The (1995)' -> ('The American President', 1995)"""
    title = raw_title.strip()

    # Take the year out of the end of the title.
    year = None
    match = _YEAR_AT_END.search(title)
    if match:
        year = int(match.group(1))
        title = title[:match.start()]

    # Remove another title in brackets, like "(a.k.a. Leon)", but only at the end.
    # A title that starts with brackets, like "(500) Days of Summer", stays as it is.
    for _ in range(2):
        stripped = _PAREN_AT_END.sub("", title)
        if stripped.strip() and stripped != title:
            title = stripped
        else:
            break

    # Move "The" back to the front: "Matrix, The" -> "The Matrix".
    # OMDb searches with the normal title, so without this many movies wouldn't be found.
    m = _ARTICLE_AT_END.search(title)
    if m:
        title = f"{m.group(1)} {title[:m.start()]}"
    else:
        m = _LAPOSTROPHE_AT_END.search(title)
        if m:
            title = f"{m.group(1)}{title[:m.start()]}"

    # A year like 0000 or 9999 is a mistake in the data, so I ignore it.
    if year is not None and not (1870 <= year <= datetime.now().year + 5):
        year = None

    return title.strip(), year


def transform_movies(raw: pd.DataFrame):
    df = raw.copy()

    # Drop rows that have no movie id or no title.
    df["movie_id"] = pd.to_numeric(df["movieId"], errors="coerce")
    bad_rows = df["movie_id"].isna() | df["title"].isna()
    if bad_rows.any():
        log.warning("Dropping %d movie rows with a missing id or title", bad_rows.sum())
    df = df[~bad_rows].copy()
    df["movie_id"] = df["movie_id"].astype(int)

    # If the same movie id appears twice, keep the first one.
    dupes = df.duplicated("movie_id").sum()
    if dupes:
        log.warning("Dropping %d duplicate movie ids (keeping the first)", dupes)
        df = df.drop_duplicates("movie_id", keep="first")

    # Keep the original title, then split it into a clean title and a year.
    df["raw_title"] = df["title"].str.strip()
    parsed = [split_title(t) for t in df["raw_title"]]
    df["title"] = [p[0] for p in parsed]
    df["release_year"] = pd.array([p[1] for p in parsed], dtype="Int64")

    # Feature engineering: decade (1994 -> 1990).
    # "Int64" (capital I) lets a number column hold empty values, so movies
    # without a year just get an empty decade.
    df["decade"] = (df["release_year"] // 10 * 10).astype("Int64")

    missing_year = df["release_year"].isna().sum()
    if missing_year:
        log.info("%d movies have no release year in the title (they stay, year is NULL)", missing_year)

    # Genres come as "Action|Adventure|Sci-Fi". I split them so that each
    # (movie, genre) pair gets its own row.
    # "(no genres listed)" is not a real genre, so those movies simply get no genre rows.
    genres = df[["movie_id", "genres"]].copy()
    genres["genre_name"] = genres["genres"].str.split("|")
    genres = genres.explode("genre_name")
    genres["genre_name"] = genres["genre_name"].str.strip()
    genres = genres[genres["genre_name"].notna()
                    & (genres["genre_name"] != "")
                    & (genres["genre_name"] != "(no genres listed)")]
    movie_genres = genres[["movie_id", "genre_name"]].drop_duplicates()

    movies = df[["movie_id", "title", "raw_title", "release_year", "decade"]]
    return movies.reset_index(drop=True), movie_genres.reset_index(drop=True)


def transform_ratings(raw: pd.DataFrame, valid_movie_ids: set):
    df = raw.copy()

    df["user_id"] = pd.to_numeric(df["userId"], errors="coerce")
    df["movie_id"] = pd.to_numeric(df["movieId"], errors="coerce")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["ts"] = pd.to_numeric(df["timestamp"], errors="coerce")

    # Remove rows with unreadable values, or a rating outside 0.5 to 5.0.
    # I can't trust those rows, and I count them in the log message below.
    good = (df[["user_id", "movie_id", "rating", "ts"]].notna().all(axis=1)
            & df["rating"].between(0.5, 5.0))
    n_bad = (~good).sum()
    df = df[good].copy()

    df["user_id"] = df["user_id"].astype(int)
    df["movie_id"] = df["movie_id"].astype(int)
    df["rated_at"] = pd.to_datetime(df["ts"], unit="s", utc=True)   # unix seconds -> a real date and time

    # Remove ratings for movies that are not in movies.csv.
    # They would break the foreign key in the database.
    orphan = ~df["movie_id"].isin(valid_movie_ids)
    n_orphan = orphan.sum()
    df = df[~orphan]

    # If a user rated the same movie twice, keep the newest rating.
    n_before = len(df)
    df = df.sort_values("rated_at").drop_duplicates(["user_id", "movie_id"], keep="last")
    n_dupes = n_before - len(df)

    log.info("Ratings cleaned: kept %d | dropped %d unreadable/out-of-range, %d orphans, %d duplicates",
             len(df), n_bad, n_orphan, n_dupes)

    return df[["user_id", "movie_id", "rating", "rated_at"]].reset_index(drop=True)


# =============================================================================
# 3. LOAD - put the clean CSV data into PostgreSQL
# =============================================================================
# The same 3 steps are used for every table, and this is what makes the script safe to run again:
#   a) copy the clean DataFrame into a temporary "staging" table
#   b) insert from the staging table into the real table with ON CONFLICT DO UPDATE,
#      so a row that already exists is updated instead of added again
#   c) the update only happens if some value really changed, so running the script
#      a second time with the same CSV files changes 0 rows
# Everything runs in one transaction. If something fails, nothing is saved.

def run_schema(engine):
    schema_sql = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    # schema.sql has many statements. The raw psycopg2 cursor can run them all in one go.
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(schema_sql)
        raw.commit()
    finally:
        raw.close()


def load_base_tables(engine, movies, movie_genres, ratings):
    with engine.begin() as conn:
        movies.to_sql("stg_movies", conn, if_exists="replace", index=False)
        movie_genres.to_sql("stg_movie_genres", conn, if_exists="replace", index=False)
        ratings.to_sql("stg_ratings", conn, if_exists="replace", index=False, chunksize=10000)

        n = conn.execute(text("""
            INSERT INTO movies (movie_id, title, raw_title, release_year, decade)
            SELECT movie_id, title, raw_title, release_year, decade FROM stg_movies
            ON CONFLICT (movie_id) DO UPDATE
               SET title = EXCLUDED.title, raw_title = EXCLUDED.raw_title,
                   release_year = EXCLUDED.release_year, decade = EXCLUDED.decade
             WHERE (movies.title, movies.raw_title, movies.release_year, movies.decade)
                   IS DISTINCT FROM
                   (EXCLUDED.title, EXCLUDED.raw_title, EXCLUDED.release_year, EXCLUDED.decade)
        """)).rowcount
        log.info("movies:        %d rows inserted/updated", n)

        conn.execute(text("""
            INSERT INTO genres (genre_name)
            SELECT DISTINCT genre_name FROM stg_movie_genres
            ON CONFLICT (genre_name) DO NOTHING
        """))

        # If a movie's genres changed in the CSV, first delete the old pairs...
        conn.execute(text("""
            DELETE FROM movie_genres mg
             WHERE mg.movie_id IN (SELECT movie_id FROM stg_movies)
               AND NOT EXISTS (
                    SELECT 1
                      FROM stg_movie_genres s
                      JOIN genres g ON g.genre_name = s.genre_name
                     WHERE s.movie_id = mg.movie_id AND g.genre_id = mg.genre_id)
        """))
        # ...then add the pairs that are missing.
        n = conn.execute(text("""
            INSERT INTO movie_genres (movie_id, genre_id)
            SELECT s.movie_id, g.genre_id
              FROM stg_movie_genres s
              JOIN genres g ON g.genre_name = s.genre_name
            ON CONFLICT DO NOTHING
        """)).rowcount
        log.info("movie_genres:  %d rows inserted", n)

        conn.execute(text("""
            INSERT INTO users (user_id)
            SELECT DISTINCT user_id FROM stg_ratings
            ON CONFLICT (user_id) DO NOTHING
        """))

        n = conn.execute(text("""
            INSERT INTO ratings (user_id, movie_id, rating, rated_at)
            SELECT user_id, movie_id, rating, rated_at FROM stg_ratings
            ON CONFLICT (user_id, movie_id) DO UPDATE
               SET rating = EXCLUDED.rating, rated_at = EXCLUDED.rated_at
             WHERE (ratings.rating, ratings.rated_at)
                   IS DISTINCT FROM (EXCLUDED.rating, EXCLUDED.rated_at)
        """)).rowcount
        log.info("ratings:       %d rows inserted/updated", n)

        # The staging tables are not needed anymore.
        for stg in ("stg_movies", "stg_movie_genres", "stg_ratings"):
            conn.execute(text(f"DROP TABLE IF EXISTS {stg}"))


# =============================================================================
# 4. ENRICH - get more movie details from the OMDb API
# =============================================================================
#
# HOW I STAY UNDER THE LIMIT OF 1000 API CALLS
# --------------------------------------------
# One API call gives the details of only one movie, and the dataset has about
# 9,700 movies. So I can't enrich all of them. This is how I use the calls wisely:
#
#   1. A fixed budget. ApiBudget counts every request, including retries and
#      the second search, because OMDb counts those too. When the budget is used
#      up, the loop stops. The default is 900, and --max-calls can change it.
#
#   2. Popular movies first. Movies are sorted by number of ratings, and the
#      ones with the most ratings go first. They matter most for the analysis.
#
#   3. Never ask twice. After each movie, I save the result (FOUND, NOT_FOUND
#      or ERROR) in the omdb_fetch_log table. In the next run these movies are
#      skipped and cost 0 calls, and the script carries on with the next ones.
#      Each movie is saved right away, so pressing Ctrl+C loses nothing.
#
#   4. Stop when the limit is reached. If OMDb says "Request limit reached!"
#      or the key is wrong, the loop stops instead of wasting calls.
#
# WHAT I DO WHEN A MOVIE IS NOT FOUND
# -----------------------------------
#   * First I search with the title and the year. The year helps to get the
#     right movie when many movies share the same title.
#   * If OMDb can't find it, I search one more time with only the title. The
#     year in MovieLens is sometimes one year different from IMDb, and that can
#     be the reason for the miss.
#   * I only accept the second result if its year is within 1 year of ours.
#     Otherwise it could be a remake or a different movie with the same name,
#     and having no data is better than having the wrong data.
#   * If it is still not found, I save NOT_FOUND in omdb_fetch_log. The movie
#     stays in the movies table with all its ratings, and I never spend another
#     call on it.
#   * For network problems (timeouts, server errors) I retry up to 3 times and
#     wait a bit longer each time. If it still fails, I save ERROR, and a later
#     run tries again (up to 3 attempts in total).

class ApiBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


def call_omdb(session, budget: ApiBudget, api_key: str, params: dict):
    """
    Makes one request to OMDb, and retries if there is a network problem.
    Returns (outcome, data_or_message). The outcome is one of:
        "ok"         -> found it, data is the movie's JSON
        "not_found"  -> OMDb doesn't know this movie
        "limit"      -> daily limit reached or our budget is used up, STOP everything
        "fatal"      -> the API key is invalid, STOP everything
        "error"      -> temporary problem, skip this movie for now
    """
    last_error = "unknown error"

    for attempt in range(1, MAX_RETRIES_PER_CALL + 1):
        if budget.remaining() == 0:
            return "limit", "local API budget used up"

        # I count the call before sending it, because even a failed request
        # can still count against the OMDb limit.
        budget.used += 1
        try:
            resp = session.get(OMDB_URL, params={**params, "apikey": api_key}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
            time.sleep(BACKOFF_SECONDS * attempt)
            continue

        if resp.status_code >= 500:
            last_error = f"server error {resp.status_code}"
            time.sleep(BACKOFF_SECONDS * attempt)
            continue

        # OMDb uses status 401 both for a wrong key and for "limit reached", so I
        # read the error message to know which one it is.
        try:
            data = resp.json()
        except ValueError:
            return "error", f"non-JSON response (HTTP {resp.status_code})"

        if data.get("Response") == "True":
            return "ok", data

        message = str(data.get("Error", "")).lower()
        if "limit" in message:
            return "limit", data.get("Error")
        if "api key" in message:
            return "fatal", data.get("Error")
        if "not found" in message:
            return "not_found", data.get("Error")
        return "error", data.get("Error") or f"HTTP {resp.status_code}"

    return "error", last_error


def _first_year(text_value):
    m = re.search(r"\d{4}", text_value or "")
    return int(m.group()) if m else None


def lookup_movie(session, budget, api_key, title, year):
    """Returns (outcome, payload_or_message, used_fallback)."""
    params = {"t": title, "type": "movie", "r": "json"}
    if year:
        params["y"] = year

    outcome, payload = call_omdb(session, budget, api_key, params)

    # The second search only makes sense if the first one used a year.
    # Without a year it would be exactly the same request again.
    if outcome == "not_found" and year:
        params.pop("y")
        outcome, payload = call_omdb(session, budget, api_key, params)
        if outcome == "ok":
            found_year = _first_year(payload.get("Year"))
            if found_year is None or abs(found_year - year) > 1:
                return "not_found", f"title matched but year {found_year} != {year}", True
        return outcome, payload, True

    return outcome, payload, False


# ---- clean the OMDb response ------------------------------------------------

def na_to_none(value):
    """OMDb writes 'N/A' when a value is missing. This turns 'N/A' and blanks into None."""
    if value is None:
        return None
    value = str(value).strip()
    return None if value == "" or value.upper() == "N/A" else value


def runtime_category(minutes):
    # Feature engineering: put the runtime into a group.
    if minutes is None:
        return None
    if minutes < 90:
        return "Short"
    if minutes <= 120:
        return "Standard"
    return "Long"


def parse_omdb(payload: dict):
    """Changes the raw OMDb text values into proper numbers, dates and lists."""
    get = lambda key: na_to_none(payload.get(key))

    runtime = None
    if get("Runtime"):
        m = re.search(r"\d+", get("Runtime"))          # "142 min" -> 142
        runtime = int(m.group()) if m else None
        if runtime == 0:
            runtime = None

    box_office = None
    if get("BoxOffice"):
        digits = re.sub(r"\D", "", get("BoxOffice"))   # "$28,341,469" -> 28341469
        box_office = int(digits) if digits else None

    imdb_rating = None
    if get("imdbRating"):
        try:
            imdb_rating = float(get("imdbRating"))
        except ValueError:
            pass

    imdb_votes = None
    if get("imdbVotes"):
        digits = re.sub(r"\D", "", get("imdbVotes"))   # "2,345,678" -> 2345678
        imdb_votes = int(digits) if digits else None

    metascore = None
    if get("Metascore") and get("Metascore").isdigit():
        metascore = int(get("Metascore"))

    released = None
    if get("Released"):
        try:
            released = datetime.strptime(get("Released"), "%d %b %Y").date()
        except ValueError:
            pass

    # The director field can be "A, B" or "A (co-director), B".
    # I remove the brackets and split it into a list of names.
    directors = []
    if get("Director"):
        no_brackets = re.sub(r"\s*\([^)]*\)", "", get("Director"))
        directors = [d.strip() for d in no_brackets.split(",") if d.strip()]

    details = {
        "imdb_id": get("imdbID"),
        "omdb_title": get("Title"),
        "omdb_year": _first_year(get("Year")),
        "mpaa_rating": get("Rated"),
        "released_date": released,
        "runtime_minutes": runtime,
        "runtime_category": runtime_category(runtime),
        "plot": get("Plot"),
        "language": get("Language"),
        "country": get("Country"),
        "awards": get("Awards"),
        "imdb_rating": imdb_rating,
        "imdb_votes": imdb_votes,
        "metascore": metascore,
        "box_office_usd": box_office,
    }
    return details, list(dict.fromkeys(directors))   # dict.fromkeys removes repeated names and keeps the order


# ---- save the result for one movie ------------------------------------------

def save_result(engine, movie_id, title, year, outcome, payload, used_fallback):
    """Saves one movie in its own small transaction, so a stopped run loses nothing."""
    status = {"ok": "FOUND", "not_found": "NOT_FOUND", "error": "ERROR"}[outcome]
    error_message = payload if outcome in ("not_found", "error") else None

    with engine.begin() as conn:
        if outcome == "ok":
            details, directors = parse_omdb(payload)
            conn.execute(text("""
                INSERT INTO movie_details (movie_id, imdb_id, omdb_title, omdb_year, mpaa_rating,
                    released_date, runtime_minutes, runtime_category, plot, language, country,
                    awards, imdb_rating, imdb_votes, metascore, box_office_usd, fetched_at)
                VALUES (:movie_id, :imdb_id, :omdb_title, :omdb_year, :mpaa_rating,
                    :released_date, :runtime_minutes, :runtime_category, :plot, :language, :country,
                    :awards, :imdb_rating, :imdb_votes, :metascore, :box_office_usd, now())
                ON CONFLICT (movie_id) DO UPDATE SET
                    imdb_id = EXCLUDED.imdb_id, omdb_title = EXCLUDED.omdb_title,
                    omdb_year = EXCLUDED.omdb_year, mpaa_rating = EXCLUDED.mpaa_rating,
                    released_date = EXCLUDED.released_date, runtime_minutes = EXCLUDED.runtime_minutes,
                    runtime_category = EXCLUDED.runtime_category, plot = EXCLUDED.plot,
                    language = EXCLUDED.language, country = EXCLUDED.country, awards = EXCLUDED.awards,
                    imdb_rating = EXCLUDED.imdb_rating, imdb_votes = EXCLUDED.imdb_votes,
                    metascore = EXCLUDED.metascore, box_office_usd = EXCLUDED.box_office_usd,
                    fetched_at = now()
            """), {"movie_id": movie_id, **details})

            # Delete the old directors of this movie and add the current ones.
            conn.execute(text("DELETE FROM movie_directors WHERE movie_id = :m"), {"m": movie_id})
            for name in directors:
                conn.execute(text("INSERT INTO directors (director_name) VALUES (:n) "
                                  "ON CONFLICT (director_name) DO NOTHING"), {"n": name})
                conn.execute(text("""
                    INSERT INTO movie_directors (movie_id, director_id)
                    SELECT :m, director_id FROM directors WHERE director_name = :n
                    ON CONFLICT DO NOTHING
                """), {"m": movie_id, "n": name})

        # Write down what happened, so we know not to ask OMDb about this movie again.
        conn.execute(text("""
            INSERT INTO omdb_fetch_log (movie_id, status, query_title, query_year, used_fallback,
                                        attempts, error_message, last_attempt_at)
            VALUES (:m, :s, :t, :y, :f, 1, :e, now())
            ON CONFLICT (movie_id) DO UPDATE SET
                status = EXCLUDED.status, query_title = EXCLUDED.query_title,
                query_year = EXCLUDED.query_year, used_fallback = EXCLUDED.used_fallback,
                attempts = omdb_fetch_log.attempts + 1,
                error_message = EXCLUDED.error_message, last_attempt_at = now()
        """), {"m": movie_id, "s": status, "t": title, "y": year,
               "f": used_fallback, "e": error_message})


def enrich_from_omdb(engine, api_key, max_calls):
    budget = ApiBudget(max_calls)

    # Find the movies that still need an API answer: movies we never asked about,
    # and movies that gave an error (up to 3 tries). They are sorted by number of
    # ratings, so the most popular movies come first.
    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT m.movie_id, m.title, m.release_year, COUNT(r.rating) AS n_ratings
              FROM movies m
              LEFT JOIN ratings r         ON r.movie_id = m.movie_id
              LEFT JOIN omdb_fetch_log l  ON l.movie_id = m.movie_id
             WHERE l.movie_id IS NULL
                OR (l.status = 'ERROR' AND l.attempts < :max_attempts)
             GROUP BY m.movie_id, m.title, m.release_year
             ORDER BY n_ratings DESC, m.movie_id
             LIMIT :cap
        """), {"max_attempts": MAX_ERROR_ATTEMPTS, "cap": budget.remaining()}).fetchall()
        already_done = conn.execute(text("SELECT COUNT(*) FROM omdb_fetch_log WHERE status <> 'ERROR'")).scalar()

    log.info("OMDb: %d movies already cached (0 calls needed), %d to fetch this run, budget %d calls",
             already_done, len(pending), budget.remaining())

    counts = {"FOUND": 0, "NOT_FOUND": 0, "ERROR": 0}
    session = requests.Session()   # keeps the connection open, which is faster for many calls

    for i, row in enumerate(pending, start=1):
        outcome, payload, used_fallback = lookup_movie(
            session, budget, api_key, row.title, row.release_year)

        # The limit is reached or the key is wrong, so there is no point in continuing.
        if outcome in ("limit", "fatal"):
            log.warning("Stopping API stage: %s", payload)
            if outcome == "fatal":
                log.error("Check OMDB_API_KEY in your .env file")
            break

        save_result(engine, row.movie_id, row.title, row.release_year, outcome, payload, used_fallback)
        counts[{"ok": "FOUND", "not_found": "NOT_FOUND", "error": "ERROR"}[outcome]] += 1

        if i % 50 == 0:
            log.info("  ...%d/%d movies done (%d calls used)", i, len(pending), budget.used)

    log.info("OMDb run finished: %d found, %d not found, %d errors | %d API calls used",
             counts["FOUND"], counts["NOT_FOUND"], counts["ERROR"], budget.used)


# =============================================================================
# main
# =============================================================================

def print_summary(engine):
    with engine.connect() as conn:
        rows = {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                for t in ("movies", "users", "ratings", "genres", "movie_genres",
                          "directors", "movie_details")}
        status = dict(conn.execute(text("SELECT status, COUNT(*) FROM omdb_fetch_log GROUP BY status")).fetchall())

    print("\n---- table sizes ----")
    for table, n in rows.items():
        print(f"{table:15s} {n:>9,}")
    print("\n---- OMDb coverage ----")
    print(f"found {status.get('FOUND', 0)}, not found {status.get('NOT_FOUND', 0)}, "
          f"errors {status.get('ERROR', 0)}, never asked {rows['movies'] - sum(status.values())}")


def main():
    parser = argparse.ArgumentParser(description="MovieLens + OMDb -> PostgreSQL pipeline")
    parser.add_argument("--data-dir", default=str(BASE_DIR / "data"), help="folder holding movies.csv and ratings.csv")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_API_BUDGET, help="max OMDb calls for this run")
    parser.add_argument("--skip-api", action="store_true", help="only load the CSV data")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    api_key = os.getenv("OMDB_API_KEY")
    if not args.skip_api and args.max_calls > 0 and not api_key:
        sys.exit("OMDB_API_KEY is not set in .env (or run with --skip-api).")

    engine = create_engine(db_url)

    run_schema(engine)

    # The main flow: extract -> transform -> load, then the API step.
    raw_movies, raw_ratings = extract_csvs(Path(args.data_dir))
    movies, movie_genres = transform_movies(raw_movies)
    ratings = transform_ratings(raw_ratings, set(movies["movie_id"]))
    load_base_tables(engine, movies, movie_genres, ratings)

    if args.skip_api:
        log.info("--skip-api given, not calling OMDb")
    else:
        enrich_from_omdb(engine, api_key, args.max_calls)

    print_summary(engine)


if __name__ == "__main__":
    main()