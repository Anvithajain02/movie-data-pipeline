# Movie Data Pipeline

## 1. Project Overview

This project is a small end to end **ETL (Extract, Transform, Load) pipeline** for movie data.

- **Extract:** reads two CSV files from the MovieLens "small" dataset (`movies.csv`, `ratings.csv`) and calls the **OMDb REST API** to get extra details like director, runtime, plot and box office.
- **Transform:** cleans the data with **pandas** (fixes titles, splits genres, validates ratings, handles missing values, converts data types) and creates new columns (**feature engineering**).
- **Load:** stores everything in a **PostgreSQL** database using a normalised schema, so the analytics team can query it with SQL.

The pipeline is **idempotent**, which means I can run `etl.py` many times and it will never create duplicate rows. It also respects the **OMDb free limit of 1000 API calls**, because it keeps track of which movies were already fetched.

At the end, `queries.sql` answers four analytical questions (best rated movie, best genres, top director, average rating per year).

**Numbers from my own run:**

| Item | Count |
|---|---|
| Movies loaded | 9,742 |
| Ratings loaded | 100,836 |
| Users | 610 |
| Genres | 19 |
| Movies enriched from OMDb | 828 found, 15 not found |
| Directors | 443 |
| API calls used | 900 |

### Data Sources

| Source | What it gives me | Link |
|---|---|---|
| **MovieLens "small" dataset** (GroupLens) | `movies.csv` (9,742 movies with genres) and `ratings.csv` (100,836 ratings from 610 users) | Dataset page: https://grouplens.org/datasets/movielens/latest/ <br> Direct download (zip): https://files.grouplens.org/datasets/movielens/ml-latest-small.zip |
| **OMDb API** (Open Movie Database) | extra details for each movie: director, plot, runtime, box office, IMDb rating | API website: https://www.omdbapi.com/ <br> Free API key: https://www.omdbapi.com/apikey.aspx |

- The free OMDb key allows **1000 requests per day**, and one request returns one movie. Example request (use your own key): `https://www.omdbapi.com/?t=Heat&y=1995&type=movie&apikey=YOUR_KEY`
- I did **not** upload the MovieLens CSV files to this repository, because the dataset has its own usage license and I do not want to redistribute it. Please download it from the link above and put `movies.csv` and `ratings.csv` in the `data/` folder.
- Dataset reference: F. Maxwell Harper and Joseph A. Konstan. 2015. *The MovieLens Datasets: History and Context.* ACM Transactions on Interactive Intelligent Systems (TiiS) 5, 4, Article 19. https://doi.org/10.1145/2827872

---

## 2. System Architecture and Database Schema

### System architecture

The pipeline reads from three sources (two CSV files and the OMDb API), runs them through the Extract, Transform and Load steps in `etl.py` (Python, pandas, requests, SQLAlchemy), and writes the result into PostgreSQL.

<p align="center">
  <a href="docs/architecture.png"><img src="docs/architecture.png" alt="System architecture diagram" width="100%"></a>
</p>

### Database schema diagram

The data is split into separate tables so nothing is repeated:
- **Core tables:** `movies`, `ratings`, `users`, `genres` and `movie_genres` (from the MovieLens CSV files)
- **OMDb tables:** `movie_details`, `directors` and `movie_directors` (from the API)
- **API cache:** `omdb_fetch_log`, which remembers which movies were already asked, so no API call is wasted
- **View:** `movie_stats`, which shows the number of ratings and the average rating of each movie

In the diagram, italic blue columns are primary keys, **FK** marks a foreign key, and the lines show the relationships.

<p align="center">
  <a href="docs/er_diagram.png"><img src="docs/er_diagram.png" alt="Database schema diagram" width="100%"></a>
</p>

---

## 3. Tech Stack

| Tool | What I used it for | Why I chose it |
|---|---|---|
| **Python 3** | the whole pipeline | The standard language for data engineering, and it has a library for everything I needed. |
| **pandas** | reading CSVs, cleaning, feature engineering | Good for column based cleaning (type conversion, `explode` to split genres, `drop_duplicates`). The dataset is small, so it fits in memory easily. |
| **requests** | calling the OMDb API | Simple HTTP library, supports sessions, timeouts and error handling. |
| **SQLAlchemy + psycopg2** | connecting Python to PostgreSQL | SQLAlchemy gives connection handling and transactions, and psycopg2 is the PostgreSQL driver. I used plain SQL for the upserts, so I stay in control of the exact queries. |
| **PostgreSQL** | the database | Free, and it supports `INSERT ... ON CONFLICT` (upsert), foreign keys, CHECK constraints and window functions like `RANK()`. All of these are used in this project. |
| **python-dotenv** | reading `.env` | Keeps the database password and API key out of the code and out of GitHub. |
| **OMDb API** ([omdbapi.com](https://www.omdbapi.com/)) | extra movie details | It was required by the assignment. It is free, and returns director, plot and box office. |
| **VS Code, pgAdmin, Git/GitHub** | writing code, checking data, submitting | VS Code for coding and the terminal, pgAdmin to look at the tables and run SQL, GitHub to share the project. |

---

## 4. Project Structure

```
movie-pipeline/
├── etl.py                    # the whole pipeline (extract, transform, load, API enrichment)
├── schema.sql                # CREATE TABLE statements (etl.py runs this automatically)
├── queries.sql               # SQL answers to the 4 analytical questions
├── verify_idempotency.py     # helper script that proves a re-run changes nothing
├── requirements.txt          # Python packages
├── .env.example              # template for the settings file (copy it to .env)
├── .gitignore
├── docs/                     # architecture and ER diagram images
└── data/                     # put movies.csv and ratings.csv here (download link in section 1)
```

---

## 5. Setup Instructions

You can use VS Code, PyCharm or just a normal terminal. The steps are the same, only the way you open the project and the terminal is different.

### Step 1: Install the requirements
- **Python 3.10 or newer** from python.org. On Windows, tick "Add Python to PATH" in the installer.
- **PostgreSQL** (version 13 or newer) from postgresql.org. Remember the password you set for the `postgres` user. **pgAdmin** comes with it.
- **An OMDb API key**. It is free: https://www.omdbapi.com/apikey.aspx. Click the activation link that comes in the email, otherwise the key will not work.
- **The MovieLens small dataset** (the "small" zip, `ml-latest-small.zip`): https://files.grouplens.org/datasets/movielens/ml-latest-small.zip (dataset page: https://grouplens.org/datasets/movielens/latest/). Unzip it, I only need `movies.csv` and `ratings.csv`.

### Step 2: Get the project
Download the project (or `git clone` it), and open the folder in your IDE.
- **VS Code:** File, Open Folder, choose `movie-pipeline`. Open the terminal with Terminal, New Terminal.
- **PyCharm:** File, Open, choose the folder. Open the Terminal tab at the bottom.
- **Any other IDE:** just open a terminal in the project folder.

### Step 3: Create a virtual environment and install packages
Windows (PowerShell):
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
Mac / Linux:
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
You should see `(venv)` at the start of the terminal line.
- **VS Code:** press Ctrl+Shift+P, choose "Python: Select Interpreter" and pick the one inside `venv`.
- **PyCharm:** Settings, Project, Python Interpreter, and choose the `venv` one.

If PowerShell does not allow activating the venv, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Step 4: Create an empty database
The tables are created by the script, but the database itself must exist first.
- **pgAdmin:** right click Databases, Create, Database, name it `movies_db`, Save.
- **or terminal:** `psql -U postgres -c "CREATE DATABASE movies_db;"`

### Step 5: Add the data files
Copy `movies.csv` and `ratings.csv` into the `data/` folder.

### Step 6: Create the `.env` file
Copy `.env.example` to `.env` and fill in your values:
```
DATABASE_URL=postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/movies_db
OMDB_API_KEY=your_real_key
```
If the password has special characters like `@` or `#`, write them URL encoded (`@` becomes `%40`).

### Step 7: Run the pipeline
First a test run that loads only the CSV data and makes no API calls:
```
python etl.py --skip-api
```
Then the full run:
```
python etl.py
```
Useful options:
- `--skip-api` loads only the CSV files
- `--max-calls 100` limits how many API calls this run can use (default is 900)

### Step 8: Check that it is idempotent
```
python verify_idempotency.py
```
It takes a snapshot of every table (row count and an md5 fingerprint of the content), runs the pipeline again, takes another snapshot and compares them. The last line should be `IDEMPOTENT: PASS`.
You can also just run `python etl.py` again and look at the log. It will say `0 rows inserted/updated` for movies and ratings.

### Step 9: Run the analytical queries
Open `queries.sql` in pgAdmin (right click `movies_db`, Query Tool) and run each query. Or from the terminal:
```
psql -U postgres -d movies_db -f queries.sql
```

### Common problems
| Error | Fix |
|---|---|
| `database "movies_db" does not exist` | Do Step 4 first. |
| `password authentication failed` | The password in `.env` is wrong, or needs URL encoding. |
| `DATABASE_URL is not set` | The file must be named exactly `.env`, not `.env.txt` or `.env.example`. |
| `Could not find ...movies.csv` | Put both CSV files inside the `data/` folder. |
| `Invalid API key!` | Check the key in `.env`, and make sure you clicked the activation email. |
| pgAdmin does not show `movies_db` | If you have more than one PostgreSQL version installed, pgAdmin may be connected to a different server than the one on port 5432. Connect to the right server, and refresh Databases. |

---

## 6. Design Choices

**1. Normalised schema (3NF style).**
The CSV has genres as `Action|Comedy` in one column, and OMDb gives directors as `"A, B"`. Both break first normal form, so I made separate `genres` and `directors` tables with bridge tables (`movie_genres`, `movie_directors`). This makes "average rating per genre" and "director with most movies" normal joins and not string matching.

**2. MovieLens `movieId` as the primary key.**
I did not create my own id. The id from the CSV never changes, so loading the same file again always lands on the same rows.

**3. Composite primary key on `ratings (user_id, movie_id)`.**
One user can rate a movie only once. This key, together with the foreign keys and the CHECK constraint (rating between 0.5 and 5.0), keeps bad data out of the database.

**4. OMDb data in separate tables.**
`movie_details` holds the API data, only for movies that were found. I kept it out of `movies` because most movies will not have API data (because of the call limit), and I did not want a wide table full of NULLs.

**5. `omdb_fetch_log` table as an API cache.**
Every movie I ask OMDb about gets a row with the status `FOUND`, `NOT_FOUND` or `ERROR`. Next time the script skips them, so no API call is wasted. This is the key to staying inside the 1000 call limit.

**6. Idempotent loading with staging tables and upserts.**
Each table is loaded into a temporary staging table first, and then moved with `INSERT ... ON CONFLICT DO UPDATE ... WHERE <values changed>`. Because of the primary keys there are no duplicates, and because of the `WHERE` check, a second run does not even rewrite unchanged rows. The whole CSV load runs in a single transaction, so it is all or nothing.

**7. `avg_rating` is a view, not a stored column.**
`movie_stats` is a view, so the average is always current when new ratings arrive.

**8. Indexes** on `ratings(movie_id)`, `movie_genres(genre_id)`, `movie_directors(director_id)` and `movies(release_year)`, because the analytical queries join and group on these columns.

**9. Feature engineering.**
- `decade` (1994 becomes 1990), made from the release year
- `runtime_category` (Short under 90 min, Standard 90 to 120, Long over 120)
- `box_office_usd`, a number parsed from text like `"$28,341,469"`
- genres split into their own table

### How the API calls are handled (1000 free calls)
The API returns **one movie per call**, and the dataset has 9,742 movies, so I cannot enrich all of them. What I did:
1. **Call budget:** a counter counts every HTTP request (retries and fallbacks too). The default is 900, which leaves some safety margin.
2. **Most rated movies first:** movies are sorted by number of ratings, so the limited calls go to the movies that matter most for the analysis.
3. **Never ask twice:** results are cached in `omdb_fetch_log`. A re-run costs 0 calls for those movies and continues with the next ones.
4. **Stop on the limit:** if OMDb returns "Request limit reached!" or an invalid key error, the loop stops cleanly.
5. **Save after every movie:** if I stop the script with Ctrl+C, nothing is lost.
6. **Retries:** for timeouts and 5xx errors, the call is retried up to 3 times with a growing wait.

### How "movie not found" is handled
1. The MovieLens title is cleaned first (`"Matrix, The (1999)"` becomes `The Matrix` and the year is taken out).
2. First search: title + year.
3. If not found, one fallback search with the title only, because the MovieLens year is sometimes off by one compared with IMDb.
4. The fallback result is accepted only if its year is within 1 year of my year. This avoids attaching a remake or a different movie with the same title.
5. If it is still not found, the movie is marked `NOT_FOUND` in `omdb_fetch_log`. It stays in the `movies` table with all its ratings, and I never query it again.

---

## 7. Assumptions

- If a user rated the same movie twice, the **latest rating** is kept.
- Ratings for movies that are not in `movies.csv`, or outside the 0.5 to 5.0 scale, are dropped (the log prints how many).
- `(no genres listed)` is not a real genre, so those movies simply have no genre rows.
- Movies without a year in the title stay in the database with a NULL year, and they are left out of the "average rating per year" query.
- Movies are matched to OMDb by **title and year**, because I only use `movies.csv` and `ratings.csv` (there is no IMDb id in them).
- The "highest rated movie" query needs at least **50 ratings**, and the "best genres" query needs at least **100 ratings**, so tiny samples cannot win by luck.
- Director data comes from OMDb, so the "top director" answer only covers the movies I could enrich. The coverage query in `queries.sql` shows how many that is.
- The free OMDb key allows 1000 calls per day, so I keep a safety margin and use 900.

---

## 8. Challenges I Faced and How I Solved Them

**1. 1000 API calls for 9,742 movies.**
At first I did not know how to enrich so many movies with so few calls. I solved it with a call budget, most rated movies first, and the `omdb_fetch_log` cache, so no movie is asked twice and every run continues where the last one stopped.

**2. Movie titles did not match the API.**
MovieLens writes titles like `Matrix, The (1999)` or with another title in brackets, and OMDb would not find them. I wrote a `split_title()` function that separates the year, removes the bracket titles and moves "The" back to the front. I used regular expressions for it.

**3. Movies that are still not found.**
Some movies just do not exist in OMDb, or the year does not match. I added one fallback search without the year, a check that the returned year is close, and a `NOT_FOUND` status in the log, so these movies are not retried and still stay in the database.

**4. Columns with multiple values.**
Genres (`Action|Comedy`) and directors (`A, B`) are many to many. I used `str.split` and `explode` in pandas, and bridge tables in PostgreSQL.

**5. Messy values from the API.**
OMDb returns the text `N/A` for missing values, and numbers as text (`"142 min"`, `"$28,341,469"`, `"2,345,678"`). I wrote small parsing functions that turn `N/A` into NULL and extract the numbers with regex.

**6. Making the pipeline idempotent.**
My first idea was to delete and reload the tables, but that would also remove the cached API results. Instead I used primary keys, `ON CONFLICT` upserts with a "only update if changed" condition, and I wrote `verify_idempotency.py` to prove it with row counts and md5 hashes of every table.

**7. Setup problems on my own machine.**
I got `database "movies_db" does not exist` because I had not created the database yet. Also, I have several PostgreSQL versions installed, and pgAdmin was showing a different server than the one my script was using on port 5432, so I could not see my tables. I found the real server with `SELECT version();` and `SHOW port;` and connected pgAdmin to that one.

---

## 9. Results

These are the outputs of the queries in `queries.sql`, run on my database (9,742 movies, 100,836 ratings, 828 movies enriched from OMDb).

### Q1. Which movie has the highest average rating?

Only movies with at least 50 ratings are counted, so a movie rated by one or two people cannot win.

```
          title           | release_year | num_ratings | avg_rating
--------------------------+--------------+-------------+------------
 The Shawshank Redemption |         1994 |         317 |       4.43
```

**Answer: The Shawshank Redemption (1994)**, with an average of 4.43 from 317 ratings.

For comparison, if I remove the minimum, the top of the list is filled with movies that have only 2 ratings, all of them 5.0, so the result would be meaningless:

```
                 title                 | num_ratings | avg_rating
---------------------------------------+-------------+------------
 Enter the Void                        |           2 |       5.00
 Belle époque                          |           2 |       5.00
 Come and See                          |           2 |       5.00
 Heidi Fleiss: Hollywood Madam         |           2 |       5.00
 Jonah Who Will Be 25 in the Year 2000 |           2 |       5.00
```

### Q2. Top 5 genres with the highest average rating

Genres with fewer than 100 ratings are ignored. A movie with several genres counts once in each of them.

```
 genre_name  | num_ratings | num_movies | avg_rating
-------------+-------------+------------+------------
 Film-Noir   |         870 |         85 |      3.920
 War         |        4859 |        381 |      3.808
 Documentary |        1219 |        438 |      3.798
 Crime       |       16681 |       1196 |      3.658
 Drama       |       41928 |       4349 |      3.656
```

**Answer: Film-Noir, War, Documentary, Crime and Drama.** Film-Noir is first with 3.92, but it has the fewest ratings of the five, so Crime and Drama are the more reliable results because they are based on many more ratings.

### Q3. Which director has the most movies in this dataset?

```
  director_name   | num_movies
------------------+------------
 Steven Spielberg |         16
```

**Answer: Steven Spielberg**, with 16 movies.

Important: director names come from the OMDb API, so this only covers the movies I was able to enrich.

```
 movies_with_director | total_movies
----------------------+--------------
                  805 |         9742
```

Only 805 of the 9,742 movies (about 8%) have director data. The API limit of 1000 calls is the reason, and the enriched movies are the most-rated ones, so the answer is for the most popular movies and not for the whole dataset.

### Q4. Average rating of movies released each year

The full output has 106 rows (1902 to 2018). `avg_rating` is the average of all ratings for that year's movies, and `avg_of_movie_averages` is the average of each movie's own average. These are some of the rows:

```
 release_year | num_movies | num_ratings | avg_rating | avg_of_movie_averages
--------------+------------+-------------+------------+-----------------------
         1908 |          1 |           1 |       4.00 |                  4.00
         1930 |          5 |          17 |       4.21 |                  4.00
         1944 |         16 |          92 |       4.04 |                  3.98
         1954 |         23 |         272 |       4.01 |                  3.51
         1957 |         33 |         215 |       4.04 |                  3.77
         1992 |        167 |        2013 |       3.35 |                  3.09
         1994 |        237 |        5296 |       3.50 |                  3.10
         1995 |        259 |        6144 |       3.44 |                  3.18
         1996 |        276 |        4509 |       3.34 |                  3.21
         1997 |        260 |        3643 |       3.35 |                  3.17
         2017 |        147 |         461 |       3.58 |                  3.38
         2018 |         41 |          91 |       3.48 |                  3.37
```

What I noticed:
- For recent years the average stays steady, between about 3.3 and 3.6.
- Some older years (like 1930, 1944, 1954 and 1957) have higher averages, around 4.0. My guess is that people mostly rate classic movies they chose to watch on purpose, but I did not test this.
- Very old years have only a few ratings (for example 1908 has just 1), so those averages should not be trusted.
- The `avg_of_movie_averages` column is lower than `avg_rating` in most years. Popular movies get many ratings and tend to be rated higher, which pulls the all-ratings average up.

---

## 10. Future Enhancements

If I had more time, or if this had to run in a real company, these are the things I would improve:

1. **Run it automatically every day.** Right now I run `etl.py` by hand. I would schedule it (Windows Task Scheduler or cron, and later a tool like Apache Airflow). Since the free API key gives 1000 calls per day, the script would add the next batch of movies every day by itself.
2. **Enrich more movies.** With a paid OMDb plan (or a different API like TMDB), all 9,742 movies could be enriched instead of only the most-rated ones.
3. **Load data faster.** For a much bigger dataset, I would use PostgreSQL's `COPY` command instead of `to_sql`, because it is a lot faster for bulk loading.
4. **Load only new data.** At the moment the whole CSV is read every time. In a real project I would load only the new or changed rows (an incremental load).
5. **Add data quality checks and tests.** For example, tests with `pytest` for functions like `split_title()` and `parse_omdb()`, and checks for nulls and duplicates after loading.
6. **Better logging.** Save the logs to a file, so I can check what happened in each run, and get an alert if a run fails.
7. **Use Docker.** Put the pipeline and the database in containers, so anyone can run it on their computer with one command and no manual setup.
8. **Move the database to the cloud.** Use a managed PostgreSQL service like AWS RDS instead of a database on my own laptop, and keep the passwords and API key in a secrets manager.
9. **Build a dashboard.** Connect the database to Power BI, Tableau or Streamlit, so the analytics team can see charts instead of writing SQL.
10. **Scale up if the data grows.** If there were millions of ratings, I would partition the `ratings` table, check the queries with `EXPLAIN ANALYZE`, and move to a data warehouse like BigQuery or Snowflake.