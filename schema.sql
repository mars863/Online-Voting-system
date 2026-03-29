CREATE TABLE IF NOT EXISTS Users (
    user_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    email     TEXT    NOT NULL UNIQUE,
    password  TEXT    NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Admins (
    admin_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    email     TEXT    NOT NULL UNIQUE,
    password  TEXT    NOT NULL,
    role      TEXT    NOT NULL DEFAULT 'admin'
              CHECK(role IN ('owner','admin'))
);

CREATE TABLE IF NOT EXISTS Elections (
    election_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    start_time  DATETIME NOT NULL,
    end_time    DATETIME NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'upcoming'
                CHECK(status IN ('upcoming','active','ended'))
);

CREATE TABLE IF NOT EXISTS Candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    election_id  INTEGER NOT NULL,
    FOREIGN KEY (election_id) REFERENCES Elections(election_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS Votes (
    vote_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    candidate_id INTEGER NOT NULL,
    election_id  INTEGER NOT NULL,
    voted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)      REFERENCES Users(user_id),
    FOREIGN KEY (candidate_id) REFERENCES Candidates(candidate_id),
    FOREIGN KEY (election_id)  REFERENCES Elections(election_id),
    UNIQUE (user_id, election_id)
);

CREATE INDEX IF NOT EXISTS idx_votes_election ON Votes(election_id);
CREATE INDEX IF NOT EXISTS idx_candidates_election ON Candidates(election_id);

CREATE VIEW IF NOT EXISTS ElectionResults AS
    SELECT
        e.election_id,
        e.title         AS election_title,
        e.status,
        c.candidate_id,
        c.name          AS candidate_name,
        COUNT(v.vote_id) AS total_votes
    FROM Elections e
    JOIN Candidates c ON c.election_id = e.election_id
    LEFT JOIN Votes v ON v.candidate_id = c.candidate_id
    GROUP BY c.candidate_id
    ORDER BY e.election_id, total_votes DESC;
