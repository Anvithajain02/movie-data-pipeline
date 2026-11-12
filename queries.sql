-- queries.sql
-- SQL queries that answer the 4 analytical questions.
-- Run them in pgAdmin (Query Tool), or from the terminal:
--     psql -U postgres -d movies_db -f queries.sql


-- ---------------------------------------------------------------------------
-- Q1. Which movie has the highest average rating?
-- ---------------------------------------------------------------------------
-- If I just sort by average rating, the winner is some unknown movie that one
-- person gave 5 stars. One rating doesn't mean much. So for the real answer I
-- only look at movies with at least 50 ratings. The second query below has no
-- minimum, so you can see the difference.

-- Q1 (main answer): highest average among movies with at least 50 ratings
SELECT  m.title,
        m.release_year,
        COUNT(*)                 AS num_ratings,
        ROUND(AVG(r.rating), 2)  AS avg_rating
FROM    ratings r
JOIN    movies  m ON m.movie_id = r.movie_id
GROUP BY m.movie_id, m.title, m.release_year
HAVING  COUNT(*) >= 50
ORDER BY AVG(r.rating) DESC, COUNT(*) DESC
LIMIT 1;

-- Q1 (for comparison): no minimum, so movies with only 1 or 2 ratings show up
SELECT  m.title,
        COUNT(*)                 AS num_ratings,
        ROUND(AVG(r.rating), 2)  AS avg_rating
FROM    ratings r
JOIN    movies  m ON m.movie_id = r.movie_id
GROUP BY m.movie_id, m.title
ORDER BY AVG(r.rating) DESC, COUNT(*) DESC
LIMIT 5;


-- ---------------------------------------------------------------------------
-- Q2. Which 5 genres have the highest average rating?
-- ---------------------------------------------------------------------------
-- A movie with 3 genres counts once in each of those 3 genres.
-- I skip genres with fewer than 100 ratings, so a very small genre can't win by luck.

SELECT  g.genre_name,
        COUNT(*)                 AS num_ratings,
        COUNT(DISTINCT mg.movie_id) AS num_movies,
        ROUND(AVG(r.rating), 3)  AS avg_rating
FROM    genres g
JOIN    movie_genres mg ON mg.genre_id = g.genre_id
JOIN    ratings r       ON r.movie_id  = mg.movie_id
GROUP BY g.genre_id, g.genre_name
HAVING  COUNT(*) >= 100
ORDER BY AVG(r.rating) DESC
LIMIT 5;


-- ---------------------------------------------------------------------------
-- Q3. Which director has the most movies in this dataset?
-- ---------------------------------------------------------------------------
-- RANK() gives rank 1 to every director who is tied for first place, so if two
-- directors have the same count, both are shown.
-- Important: the director names come from the OMDb API, so this only counts
-- the movies I could get from the API (the 1000 call limit). The second
-- query shows how many movies that is.

WITH director_counts AS (
    SELECT  d.director_name,
            COUNT(*) AS num_movies
    FROM    directors d
    JOIN    movie_directors md ON md.director_id = d.director_id
    GROUP BY d.director_id, d.director_name
),
ranked AS (
    SELECT  *, RANK() OVER (ORDER BY num_movies DESC) AS rnk
    FROM    director_counts
)
SELECT director_name, num_movies
FROM   ranked
WHERE  rnk = 1
ORDER BY director_name;

-- Q3 coverage check: how many movies have a director, out of all movies
SELECT  COUNT(DISTINCT md.movie_id)  AS movies_with_director,
        (SELECT COUNT(*) FROM movies) AS total_movies
FROM    movie_directors md;


-- ---------------------------------------------------------------------------
-- Q4. What is the average rating of the movies released each year?
-- ---------------------------------------------------------------------------
-- "Average rating of a year" can mean two things, so I show both:
--   avg_rating            = average of all the ratings given to that year's movies
--   avg_of_movie_averages = average of each movie's own average (each movie counts once)
-- Movies that have no year in the title are left out.

WITH per_movie AS (
    SELECT  m.movie_id,
            m.release_year,
            COUNT(r.rating) AS n,
            SUM(r.rating)   AS total,
            AVG(r.rating)   AS movie_avg
    FROM    movies m
    JOIN    ratings r ON r.movie_id = m.movie_id
    WHERE   m.release_year IS NOT NULL
    GROUP BY m.movie_id, m.release_year
)
SELECT  release_year,
        COUNT(*)                          AS num_movies,
        SUM(n)                            AS num_ratings,
        ROUND(SUM(total) / SUM(n), 2)     AS avg_rating,
        ROUND(AVG(movie_avg), 2)          AS avg_of_movie_averages
FROM    per_movie
GROUP BY release_year
ORDER BY release_year;