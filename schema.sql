-- schema.sql
-- Creates all the tables for the movie project in PostgreSQL.
-- Every statement uses IF NOT EXISTS, so etl.py can run this file every time
-- without errors, even when the tables are already there.
--
-- How the tables connect:
--
--   users ---< ratings >--- movies ---< movie_genres >--- genres
--                             |
--                             |--- movie_details   (extra info from OMDb, one row per movie)
--                             |--- omdb_fetch_log  (did we already ask OMDb about this movie?)
--                             '---< movie_directors >--- directors
--
-- "---<" means one to many. For example, one movie has many ratings.


-- ---------------------------------------------------------------------------
-- Main tables from the MovieLens CSV files
-- ---------------------------------------------------------------------------

-- I use the movieId from the CSV as the primary key instead of creating my own.
-- That way, loading the same CSV again always points to the same rows.
CREATE TABLE IF NOT EXISTS movies (
    movie_id      INTEGER PRIMARY KEY,
    title         TEXT     NOT NULL,          -- cleaned title, like "The Matrix" (no year)
    raw_title     TEXT     NOT NULL,          -- title exactly as it was in the CSV
    release_year  SMALLINT CHECK (release_year BETWEEN 1870 AND 2100),  -- empty if the title has no year
    decade        SMALLINT,                   -- new column: 1994 becomes 1990
    CONSTRAINT chk_movies_decade
        CHECK (decade IS NULL OR decade = release_year / 10 * 10)
);

-- The CSV only has user ids, nothing else about users. I still keep a small
-- table so every rating has to belong to a real user.
CREATE TABLE IF NOT EXISTS users (
    user_id  INTEGER PRIMARY KEY
);

-- In the CSV, genres look like "Action|Comedy" in a single column. That is hard
-- to query, so each genre gets its own row here, and movie_genres links
-- movies to genres (a movie can have many genres, a genre has many movies).
CREATE TABLE IF NOT EXISTS genres (
    genre_id    SERIAL PRIMARY KEY,
    genre_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS movie_genres (
    movie_id  INTEGER NOT NULL REFERENCES movies (movie_id) ON DELETE CASCADE,
    genre_id  INTEGER NOT NULL REFERENCES genres (genre_id) ON DELETE CASCADE,
    PRIMARY KEY (movie_id, genre_id)          -- the same pair can't be added twice
);

-- One row for each (user, movie) pair. The primary key on both columns is what
-- stops duplicates when the script runs again: the same rating just matches
-- the existing row instead of being added a second time.
CREATE TABLE IF NOT EXISTS ratings (
    user_id   INTEGER      NOT NULL REFERENCES users (user_id),
    movie_id  INTEGER      NOT NULL REFERENCES movies (movie_id) ON DELETE CASCADE,
    rating    NUMERIC(2,1) NOT NULL CHECK (rating BETWEEN 0.5 AND 5.0),
    rated_at  TIMESTAMPTZ  NOT NULL,
    PRIMARY KEY (user_id, movie_id)
);


-- ---------------------------------------------------------------------------
-- Tables for the data that comes from the OMDb API
-- ---------------------------------------------------------------------------

-- A movie can have more than one director, and one director can make many
-- movies. If I kept "A, B" as plain text in one column, I couldn't count
-- movies per director. So directors get their own table, plus a linking table.
CREATE TABLE IF NOT EXISTS directors (
    director_id    SERIAL PRIMARY KEY,
    director_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS movie_directors (
    movie_id     INTEGER NOT NULL REFERENCES movies (movie_id)       ON DELETE CASCADE,
    director_id  INTEGER NOT NULL REFERENCES directors (director_id) ON DELETE CASCADE,
    PRIMARY KEY (movie_id, director_id)
);

-- Extra details from OMDb, only for movies the API found. It is a separate table
-- because most movies won't have this data (the API limit is 1000 calls),
-- and I didn't want a lot of empty columns in the movies table.
CREATE TABLE IF NOT EXISTS movie_details (
    movie_id          INTEGER PRIMARY KEY REFERENCES movies (movie_id) ON DELETE CASCADE,
    imdb_id           TEXT,
    omdb_title        TEXT,
    omdb_year         SMALLINT,
    mpaa_rating       TEXT,
    released_date     DATE,
    runtime_minutes   SMALLINT CHECK (runtime_minutes > 0),
    runtime_category  TEXT CHECK (runtime_category IN ('Short', 'Standard', 'Long')),  -- new column made from runtime
    plot              TEXT,
    language          TEXT,
    country           TEXT,
    awards            TEXT,
    imdb_rating       NUMERIC(3,1) CHECK (imdb_rating BETWEEN 0 AND 10),
    imdb_votes        INTEGER,
    metascore         SMALLINT,
    box_office_usd    BIGINT CHECK (box_office_usd >= 0),   -- "$28,341,469" is stored as 28341469
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keeps track of every movie we have already asked OMDb about.
-- FOUND or NOT_FOUND means we never ask again, so no API calls are wasted.
-- ERROR means something went wrong (like a timeout), so a later run tries again,
-- up to 3 times.
CREATE TABLE IF NOT EXISTS omdb_fetch_log (
    movie_id         INTEGER PRIMARY KEY REFERENCES movies (movie_id) ON DELETE CASCADE,
    status           TEXT NOT NULL CHECK (status IN ('FOUND', 'NOT_FOUND', 'ERROR')),
    query_title      TEXT,
    query_year       SMALLINT,
    used_fallback    BOOLEAN NOT NULL DEFAULT FALSE,   -- true if we had to search again without the year
    attempts         INTEGER NOT NULL DEFAULT 1,
    error_message    TEXT,
    last_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------------
-- Indexes (they make the queries faster)
-- ---------------------------------------------------------------------------

-- Primary keys already create indexes for most lookups. The ratings key starts
-- with user_id, so finding all ratings of one movie needs its own index.
-- The average rating queries all depend on this one.
CREATE INDEX IF NOT EXISTS idx_ratings_movie        ON ratings (movie_id);
CREATE INDEX IF NOT EXISTS idx_movie_genres_genre   ON movie_genres (genre_id);
CREATE INDEX IF NOT EXISTS idx_movie_directors_dir  ON movie_directors (director_id);
CREATE INDEX IF NOT EXISTS idx_movies_year          ON movies (release_year);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status     ON omdb_fetch_log (status);


-- ---------------------------------------------------------------------------
-- View
-- ---------------------------------------------------------------------------

-- Shows the number of ratings and the average rating for each movie.
-- It is a view and not a saved column, so the average is always up to date
-- when new ratings are added.
CREATE OR REPLACE VIEW movie_stats AS
SELECT  m.movie_id,
        m.title,
        m.release_year,
        COUNT(r.rating)            AS rating_count,
        ROUND(AVG(r.rating), 2)    AS avg_rating
FROM    movies m
LEFT JOIN ratings r ON r.movie_id = m.movie_id
GROUP BY m.movie_id, m.title, m.release_year;